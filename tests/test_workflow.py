"""离线行为与 HTTP 契约验证；不会访问任何真实翻译服务。"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import plistlib
import queue
import secrets
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from http.client import IncompleteRead
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request

from adapters import discover
from adapters.aliyun import Adapter as Aliyun
from adapters.azure import Adapter as Azure
from adapters.baidu import Adapter as Baidu
from adapters.volc import Adapter as Volc
from cache import TranslationCache
from config import BUNDLE_ID, ROOT, ConfigError, ensure_config, load_config
from http_client import HttpResponse, TransportError, send
from main import configure_logging, run
from models import TranslationRequest, TranslationResult, failure
from output import format_results
from parser import Query, QueryError, parse_query
from scheduler import Scheduler


def settings(engine):
    """仅在测试运行时生成模拟凭据，不保存任何真实或可用密钥。"""
    return {"enable": True, "ak": hashlib.sha256(b"fixture identity").hexdigest(),
            "sk": hashlib.sha256(b"fixture signing material").hexdigest(),
            "appid": hashlib.sha256(b"fixture application").hexdigest(),
            "appkey": hashlib.sha256(b"fixture application material").hexdigest(),
            "key": secrets.token_hex(16), "region": "cn-north-1" if engine == "volc" else "cn-hangzhou"}


class LocalFiles(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix=".verification-", dir=ROOT)
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)
        self.config = load_config(self.path / "data")


class ParserTests(unittest.TestCase):
    def setUp(self):
        self.aliases = {"v": "volc", "b": "baidu", "a": "aliyun", "m": "azure"}

    def test_prefix_target_and_whitespace(self):
        query = parse_query("v:set:en  你好\n世界 ", self.aliases)
        self.assertEqual((query.engine, query.request.to_lang, query.request.text),
                         ("volc", "en", " 你好\n世界 "))

    def test_all_target(self):
        query = parse_query("all:set:ja hello", self.aliases)
        self.assertTrue(query.all_engines)
        self.assertEqual(query.request.to_lang, "ja")

    def test_colons_shell_text_and_escapes(self):
        for value in ("https://example.invalid/a:b", "a; $(whoami) `echo x` ' \"", "unknown:hello"):
            self.assertEqual(parse_query(value, self.aliases).request.text, value)
        self.assertEqual(parse_query("-- v:hello", self.aliases).request.text, "v:hello")
        self.assertEqual(parse_query("v:-- set:en hello", self.aliases).request.text, "set:en hello")

    def test_invalid_language(self):
        for value in ("set:", "set:xx hi", "v:set:xx hello"):
            with self.assertRaises(QueryError):
                parse_query(value, self.aliases)

    def test_custom_adapter_prefix(self):
        query = parse_query("x:hello", {"x": "extra"}, "de")
        self.assertEqual((query.engine, query.request.to_lang), ("extra", "de"))


class ConfigTests(LocalFiles):
    def test_created_private_and_never_overwritten(self):
        path = ensure_config(self.path / "data")
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        path.write_text('{"default_engine":"azure","engines":{}}', encoding="utf-8")
        self.assertEqual(load_config(path.parent)["engines"], {})
        self.assertEqual(load_config(path.parent)["default_engine"], "azure")

    def test_invalid_config_does_not_reset(self):
        path = self.path / "data" / "config.json"
        path.write_text("not json", encoding="utf-8")
        with self.assertRaises(ConfigError):
            load_config(path.parent)
        self.assertEqual(path.read_text(), "not json")

    def test_invalid_types_and_limits(self):
        for field, value in (("fallback_enable", "false"), ("request_timeout_seconds", 0),
                             ("request_timeout_seconds", 11), ("cache_max_entries", 1.5),
                             ("fallback_order", "azure"), ("engines", []), ("target_lang_default", [])):
            with self.subTest(field=field, value=value):
                (self.path / "data" / "config.json").write_text(json.dumps({field: value}))
                with self.assertRaises(ConfigError):
                    load_config(self.path / "data")


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.request = TranslationRequest("你好 &+\n世界", "en")

    def test_discovery(self):
        adapters, errors = discover()
        self.assertEqual(set(adapters), {"volc", "baidu", "aliyun", "azure"})
        self.assertEqual(errors, [])

    def test_no_credentials_never_contacts_transport(self):
        for adapter in (Volc, Baidu, Aliyun, Azure):
            calls = []
            instance = adapter({"enable": True}, lambda *args: calls.append(args))
            self.assertEqual(instance.translate(self.request, 1).error_code, "config")
            self.assertEqual(calls, [])

    def test_disabled_never_contacts_transport(self):
        self.assertEqual(Azure({}).translate(self.request, 1).error_code, "disabled")

    def test_empty_input_rejected_by_adapter(self):
        self.assertEqual(Azure(settings("azure")).translate(TranslationRequest(" \n"), 1).error_code, "invalid_input")

    def test_limits_count_bytes_and_characters(self):
        self.assertIsNone(Baidu(settings("baidu")).validate(TranslationRequest("中" * 2000)))
        self.assertEqual(Baidu(settings("baidu")).validate(TranslationRequest("中" * 2001)).error_code, "input_too_long")
        for cls in (Volc, Aliyun):
            instance = cls(settings(cls.engine))
            self.assertIsNone(instance.validate(TranslationRequest("😀" * 5000)))
            self.assertEqual(instance.validate(TranslationRequest("x" * 5001)).error_code, "input_too_long")

    def test_baidu_raw_text_signature_and_language(self):
        config = settings("baidu")
        adapter = Baidu(config)
        with patch("adapters.baidu.secrets.token_hex", return_value="fixture-nonce"):
            wire = adapter.build_request(TranslationRequest(self.request.text, "ja"))
        body = parse_qs(wire.data.decode())
        expected = hashlib.md5((config["appid"] + self.request.text + body["salt"][0] + config["appkey"]).encode()).hexdigest()
        self.assertEqual(body["sign"], [expected])
        self.assertEqual(body["q"], [self.request.text])
        self.assertEqual(body["to"], ["jp"])

    def test_baidu_multiline_and_errors(self):
        adapter = Baidu(settings("baidu"))
        payload = {"from": "jp", "to": "en", "trans_result": [{"dst": "first"}, {"dst": "second"}]}
        result = adapter.parse_response(self.request, HttpResponse(200, payload, {}))
        self.assertEqual((result.translated_text, result.from_lang), ("first\nsecond", "ja"))
        for code, expected in (("54001", "auth"), ("52003", "auth"), ("54004", "quota"), ("54003", "rate_limit")):
            result = adapter.parse_response(self.request, HttpResponse(200, {"error_code": code}, {}))
            self.assertEqual(result.error_code, expected)

    def test_volc_signature_on_actual_wire_body(self):
        adapter = Volc(settings("volc"))
        with patch("adapters.volc.datetime") as clock:
            clock.now.return_value = datetime(2026, 11, 5, tzinfo=timezone.utc)
            wire = adapter.build_request(self.request)
        body = json.loads(wire.data)
        self.assertEqual(body, {"TargetLanguage": "en", "TextList": [self.request.text]})
        self.assertNotIn("SourceLanguage", body)
        self.assertEqual(wire.get_header("X-content-sha256"), hashlib.sha256(wire.data).hexdigest())
        self.assertIn("20261105/cn-north-1/translate/request", wire.get_header("Authorization"))
        self.verify_wire_signature(wire, adapter.settings["sk"], "volc")

    def test_aliyun_signature_and_form(self):
        adapter = Aliyun(settings("aliyun"))
        with patch("adapters.aliyun.datetime") as clock, patch("adapters.aliyun.uuid4", return_value="fixture-nonce"):
            clock.now.return_value = datetime(2026, 11, 5, tzinfo=timezone.utc)
            wire = adapter.build_request(self.request)
        body = parse_qs(wire.data.decode())
        self.assertEqual(body["SourceText"], [self.request.text])
        self.assertEqual(body["SourceLanguage"], ["auto"])
        self.assertEqual(body["FormatType"], ["text"])
        self.assertEqual(wire.get_header("X-acs-content-sha256"), hashlib.sha256(wire.data).hexdigest())
        self.verify_wire_signature(wire, adapter.settings["sk"], "aliyun")

    def verify_wire_signature(self, wire, secret, provider):
        # 服务端视角：仅使用最终 HTTP 字节和 Authorization 中声明的字段验证。
        headers = {key.lower(): value for key, value in wire.header_items()}
        auth = headers["authorization"]
        algorithm, values = auth.split(" ", 1)
        fields = dict(part.strip().split("=", 1) for part in values.split(","))
        signed = fields["SignedHeaders"]
        url = urlsplit(wire.full_url)
        canonical = ("POST\n" + url.path + "\n" + url.query + "\n" +
                     "".join(key + ":" + headers[key] + "\n" for key in signed.split(";")) +
                     "\n" + signed + "\n" + hashlib.sha256(wire.data).hexdigest())
        hashed = hashlib.sha256(canonical.encode()).hexdigest()
        key = secret.encode()
        if provider == "volc":
            scope = fields["Credential"].split("/", 1)[1]
            for part in scope.split("/"):
                key = hmac.digest(key, part.encode(), "sha256")
            data = algorithm + "\n" + headers["x-date"] + "\n" + scope + "\n" + hashed
        else:
            data = algorithm + "\n" + hashed
        self.assertEqual(fields["Signature"], hmac.digest(key, data.encode(), "sha256").hex())

    def test_azure_headers_and_source_omission(self):
        config = settings("azure")
        config["region"] = ""
        adapter = Azure(config)
        wire = adapter.build_request(TranslationRequest("hello"))
        self.assertEqual(parse_qs(urlsplit(wire.full_url).query)["to"], ["zh-Hans"])
        self.assertNotIn("from", parse_qs(urlsplit(wire.full_url).query))
        self.assertIsNone(wire.get_header("Ocp-apim-subscription-region"))
        config["region"] = "eastasia"
        self.assertEqual(adapter.build_request(self.request).get_header("Ocp-apim-subscription-region"), "eastasia")

    def test_azure_quota_is_not_generic_auth(self):
        adapter = Azure(settings("azure"))
        self.assertEqual(adapter.parse_response(self.request, HttpResponse(403, {"error": {"code": 403001}}, {})).error_code, "quota")
        self.assertEqual(adapter.parse_response(self.request, HttpResponse(403, {"error": {"code": 403000}}, {})).error_code, "auth")

    def test_success_shapes_and_unknown_detection(self):
        fixtures = ((Volc, {"TranslationList": [{"Translation": "hello", "DetectedSourceLanguage": "zh"}], "ResponseMetadata": {"RequestId": "req-1"}}),
                    (Aliyun, {"Code": 200, "Data": {"Translated": "hello", "DetectedLanguage": "zh"}}),
                    (Azure, [{"detectedLanguage": {"language": "zh-Hans"}, "translations": [{"text": "hello"}]}]))
        for cls, payload in fixtures:
            result = cls(settings(cls.engine)).parse_response(self.request, HttpResponse(200, payload, {}))
            self.assertEqual((result.success, result.translated_text, result.from_lang), (True, "hello", "zh"))
        result = Azure(settings("azure")).parse_response(self.request, HttpResponse(200, [{"translations": [{"text": "hello"}]}], {}))
        self.assertEqual(result.from_lang, "und")

    def test_volc_business_failure_under_http_200(self):
        adapter = Volc(settings("volc"))
        for code, expected in (("SignatureDoesNotMatch", "auth"), ("-429", "rate_limit"), ("-415", "unsupported_language")):
            result = adapter.parse_response(self.request, HttpResponse(200, {"ResponseMetadata": {"Error": {"Code": code}}}, {}))
            self.assertEqual(result.error_code, expected)

    def test_response_and_transport_failures_are_safe(self):
        for payload in (None, {"Code": 200, "Data": {}}, {"Code": 200, "Data": {"Translated": ""}}):
            instance = Aliyun(settings("aliyun"), lambda *_: HttpResponse(200, payload, {}))
            self.assertFalse(instance.translate(self.request, 1).success)
        def fail(*_):
            raise TransportError("fixture-timeout")
        self.assertEqual(Azure(settings("azure"), fail).translate(self.request, 1).error_code, "network")


class CacheTests(LocalFiles):
    def setUp(self):
        super().setUp()
        self.cache = TranslationCache(self.path / "cache", self.config)
        self.request = TranslationRequest("hello")
        self.result = TranslationResult(True, "volc", "hello", "你好", "en", "zh")

    def test_separates_engine_target_and_source(self):
        self.cache.put(self.request, self.result)
        self.assertTrue(self.cache.get("volc", self.request).cached)
        self.assertIsNone(self.cache.get("baidu", self.request))
        self.assertIsNone(self.cache.get("volc", TranslationRequest("hello", "ja")))
        self.assertIsNone(self.cache.get("volc", TranslationRequest("hello", "zh", "en")))

    def test_failure_and_long_text_are_not_cached(self):
        self.cache.put(self.request, failure("volc", self.request, "network"))
        self.assertIsNone(self.cache.get("volc", self.request))
        self.cache.max_text = 1
        self.cache.put(self.request, self.result)
        self.assertFalse(self.cache.path.exists())

    def test_expiry_and_corruption(self):
        self.cache.put(self.request, self.result)
        with patch("cache.time.time", return_value=time.time() + 301):
            self.assertIsNone(self.cache.get("volc", self.request))
        self.cache.path.write_text("{broken", encoding="utf-8")
        self.assertIsNone(self.cache.get("volc", self.request))
        self.cache.put(self.request, self.result)
        self.assertTrue(self.cache.get("volc", self.request).success)

    def test_entry_limit_and_private_file(self):
        self.cache.max_entries = 2
        for text in ("one", "two", "three"):
            self.cache.put(TranslationRequest(text), TranslationResult(True, "volc", text, "译文"))
        self.assertEqual(len(json.loads(self.cache.path.read_text())), 2)
        self.assertEqual(self.cache.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(list(self.cache.directory.glob(".cache-*")), [])

    def test_busy_lock_is_optional(self):
        with self.cache.lock():
            self.cache.put(self.request, self.result)
        self.assertFalse(self.cache.path.exists())

    def test_atomic_failure_preserves_existing_file(self):
        self.cache.put(self.request, self.result)
        before = self.cache.path.read_bytes()
        with patch("cache.os.replace", side_effect=OSError("fixture")):
            self.cache.put(TranslationRequest("changed"), TranslationResult(True, "volc", "changed", "改变"))
        self.assertEqual(self.cache.path.read_bytes(), before)
        self.assertEqual(list(self.cache.directory.glob(".cache-*")), [])


class FakeAdapter:
    def __init__(self, engine, calls, outcome="ok", delay=0, preflight=None):
        self.engine, self.calls, self.outcome, self.delay, self.preflight = engine, calls, outcome, delay, preflight

    def validate(self, request):
        return failure(self.engine, request, self.preflight) if self.preflight else None

    def translate(self, request, timeout):
        self.calls.append(self.engine)
        time.sleep(self.delay)
        return (TranslationResult(True, self.engine, request.text, "译文") if self.outcome == "ok"
                else failure(self.engine, request, self.outcome))


class SchedulerTests(LocalFiles):
    def setUp(self):
        super().setUp()
        self.config["cache_enabled"] = False
        self.calls = []
        self.adapters = {name: FakeAdapter(name, self.calls) for name in self.config["fallback_order"]}

    def scheduler(self):
        return Scheduler(self.config, self.adapters, self.path / "cache")

    def test_default_fallback_marks_actual_provider(self):
        self.adapters["volc"].outcome = "quota"
        result = self.scheduler().translate(Query(TranslationRequest("hello")))[0]
        self.assertEqual((result.engine, result.fallback_from), ("azure", "volc"))
        self.assertEqual(self.calls, ["volc", "azure"])

    def test_explicit_provider_never_falls_back(self):
        self.adapters["volc"].outcome = "auth"
        result = self.scheduler().translate(Query(TranslationRequest("hello"), "volc"))[0]
        self.assertFalse(result.success)
        self.assertEqual(self.calls, ["volc"])

    def test_three_attempts_no_duplicates(self):
        self.config["fallback_order"] = ["volc", "volc", "azure", "azure", "aliyun", "baidu"]
        for adapter in self.adapters.values():
            adapter.outcome = "network"
        self.scheduler().translate(Query(TranslationRequest("hello")))
        self.assertEqual(self.calls, ["volc", "azure", "aliyun"])

    def test_disabled_and_config_do_not_consume_attempt(self):
        self.adapters["volc"].preflight = "config"
        self.adapters["azure"].preflight = "disabled"
        result = self.scheduler().translate(Query(TranslationRequest("hello")))[0]
        self.assertEqual(result.engine, "aliyun")
        self.assertEqual(self.calls, ["aliyun"])

    def test_input_error_does_not_fallback(self):
        self.adapters["volc"].preflight = "input_too_long"
        self.scheduler().translate(Query(TranslationRequest("hello")))
        self.assertEqual(self.calls, [])

    def test_all_parallel_and_stable_order(self):
        entered = queue.Queue()
        release = threading.Event()
        for adapter in self.adapters.values():
            def worker(request, timeout, adapter=adapter):
                entered.put(adapter.engine)
                release.wait(1)
                return TranslationResult(True, adapter.engine, request.text, "译文")
            adapter.translate = worker
        output = queue.Queue()
        thread = threading.Thread(target=lambda: output.put(self.scheduler().translate(Query(TranslationRequest("hello"), all_engines=True))))
        thread.start()
        try:
            self.assertEqual({entered.get(timeout=1) for _ in range(4)}, set(self.adapters))
        finally:
            release.set()
            thread.join(2)
        self.assertEqual([row.engine for row in output.get(timeout=1)], self.config["fallback_order"])

    def test_all_partial_failure_no_fallback(self):
        self.adapters["azure"].outcome = "rate_limit"
        result = self.scheduler().translate(Query(TranslationRequest("hello"), all_engines=True))
        self.assertEqual(sum(row.success for row in result), 3)
        self.assertEqual(len(self.calls), 4)

    def test_total_deadline_does_not_join_late_worker(self):
        self.config["query_timeout_seconds"] = 0.1
        self.adapters["volc"].delay = 0.5
        started = time.monotonic()
        result = self.scheduler().translate(Query(TranslationRequest("hello")))
        self.assertLess(time.monotonic() - started, 0.35)
        self.assertFalse(result[0].success)
        self.assertEqual(self.calls, ["volc"])

    def test_no_enabled_engines(self):
        self.config["engines"] = {}
        result = self.scheduler().translate(Query(TranslationRequest("hello"), all_engines=True))
        self.assertEqual(result[0].error_code, "config")


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.inbox = queue.Queue()
        inbox = self.inbox
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                inbox.put(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
                self.send_response(429)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error":{"code":429000}}')
            def do_GET(self):
                if self.path == "/redirect":
                    self.send_response(302)
                    self.send_header("Location", "/unexpected")
                    self.end_headers()
                elif self.path == "/slow":
                    time.sleep(0.2)
                    self.send_response(200)
                    self.end_headers()
                else:
                    inbox.put(b"unexpected")
                    self.send_response(500)
                    self.end_headers()
                    self.wfile.write(b"<html>gateway failure</html>")
            def log_message(self, *args):
                pass
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def test_http_error_body_and_actual_post_bytes(self):
        content = '你好 + "\n'.encode()
        response = send(Request(self.url, data=content), 1)
        self.assertEqual(self.inbox.get(timeout=1), content)
        self.assertEqual(response.status, 429)
        self.assertEqual(response.payload["error"]["code"], 429000)

    def test_redirect_not_followed(self):
        response = send(Request(self.url + "/redirect"), 1)
        self.assertEqual(response.status, 302)
        self.assertTrue(self.inbox.empty())

    def test_html_failure_is_bounded(self):
        response = send(Request(self.url + "/html"), 1)
        self.assertEqual((response.status, response.payload), (500, None))

    def test_socket_timeout(self):
        with self.assertRaises(TransportError):
            send(Request(self.url + "/slow"), 0.05)

    def test_connection_cut_short_is_network_error(self):
        with patch("http_client.build_opener") as opener:
            opener.return_value.open.return_value.read.side_effect = IncompleteRead(b"partial")
            with self.assertRaises(TransportError):
                send(Request(self.url), 1)


class OutputAndEntryTests(LocalFiles):
    def environment(self):
        return {**os.environ, "alfred_workflow_data": str(self.path / "data"),
                "alfred_workflow_cache": str(self.path / "cache"), "PYTHONDONTWRITEBYTECODE": "1"}

    def test_copy_keeps_newline_and_literal_placeholders(self):
        text = "第一段\n第二段 {date} $(echo x)"
        result = format_results([TranslationResult(True, "volc", "hello", text),
                                 failure("azure", TranslationRequest("hello"), "auth")], {})
        self.assertEqual(result["items"][0]["arg"], text)
        self.assertFalse(result["items"][1]["valid"])
        self.assertNotIn("arg", result["items"][1])

    def test_shell_missing_interpreter_returns_json(self):
        env = self.environment()
        env["PYTHON_BINARY"] = "definitely-not-an-interpreter"
        process = subprocess.run(["bash", "bootstrap.sh", "translate", "hello"], cwd=ROOT,
                                 env=env, capture_output=True, text=True, timeout=3)
        self.assertEqual(process.returncode, 0)
        self.assertFalse(json.loads(process.stdout)["items"][0]["valid"])
        self.assertEqual(process.stderr, "")

    def test_shell_and_cli_safe_json(self):
        env = self.environment()
        env["PYTHON_BINARY"] = sys.executable
        for text in ("", "all:hello", "-- $(echo not-executed) `echo x` \" ' \n", "set:xx hello"):
            process = subprocess.run(["bash", "bootstrap.sh", "translate", text], cwd=ROOT,
                                     env=env, capture_output=True, text=True, timeout=3)
            self.assertEqual(process.returncode, 0)
            self.assertIn("items", json.loads(process.stdout))
            self.assertEqual(process.stderr, "")
        self.assertFalse((self.path / "cache" / "translations.json").exists())

    def test_configure_only_opens_the_private_file(self):
        with patch.dict(os.environ, self.environment()), patch("main.subprocess.run") as opened:
            result = run(["configure"])
        self.assertEqual(opened.call_args.args[0], ["open", "-t", str(self.path / "data" / "config.json")])
        self.assertFalse(result["items"][0]["valid"])

    def test_logs_have_metadata_not_text(self):
        import logging
        configure_logging(self.path / "cache", True)
        scheduler = Scheduler(self.config, {"volc": FakeAdapter("volc", [])}, self.path / "cache")
        scheduler.translate(Query(TranslationRequest("private-text-that-must-not-appear"), "volc"))
        logger = logging.getLogger("multitranslate")
        for handler in logger.handlers:
            handler.flush()
        log = (self.path / "cache" / "workflow.log").read_text()
        self.assertIn("engine=volc", log)
        self.assertNotIn("private-text", log)
        self.assertNotIn(str(ROOT), log)
        for handler in logger.handlers:
            handler.close()
        configure_logging(self.path / "cache", False)

    def test_workflow_links_and_argv(self):
        metadata = plistlib.loads((ROOT / "info.plist").read_bytes())
        self.assertEqual(metadata["bundleid"], BUNDLE_ID)
        nodes = {node["type"]: node for node in metadata["objects"]}
        script = nodes["alfred.workflow.input.scriptfilter"]
        self.assertEqual(script["config"]["scriptargtype"], 1)
        self.assertNotIn("{query}", script["config"]["script"])
        self.assertFalse(script["config"]["alfredfiltersresults"])
        dest = metadata["connections"][script["uid"]][0]["destinationuid"]
        self.assertEqual(dest, nodes["alfred.workflow.output.clipboard"]["uid"])
        self.assertFalse(nodes["alfred.workflow.output.clipboard"]["config"]["autopaste"])


if __name__ == "__main__":
    unittest.main()
