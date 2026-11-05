"""离线行为与 HTTP 契约验证；不会访问任何真实翻译服务。"""
from __future__ import annotations

import hashlib
import fcntl
import hmac
import json
import os
import plistlib
import queue
import secrets
import shutil
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
from adapters.youdao import Adapter as Youdao
from build_workflow import build
from cache import TranslationCache
from config import BUNDLE_ID, ROOT, ConfigError, ensure_config, load_config
from http_client import HttpResponse, TransportError, send
from main import configure_logging, run
from models import TranslationRequest, TranslationResult, failure
from output import format_results
from panel_import import MARKER, MARKER_CONTENT, LOCK, _atomic_write, _read_preferences, _reload_workflow, import_json_to_panel
from parser import Query, QueryError, parse_query
from scheduler import Scheduler


def settings(engine):
    """仅在测试运行时生成模拟凭据，不保存任何真实或可用密钥。"""
    return {"enable": True, "ak": hashlib.sha256(b"fixture identity").hexdigest(),
            "sk": hashlib.sha256(b"fixture signing material").hexdigest(),
            "appid": hashlib.sha256(b"fixture application").hexdigest(),
            "appkey": hashlib.sha256(b"fixture application material").hexdigest(),
            "app_key": secrets.token_hex(12), "app_secret": secrets.token_hex(16),
            "key": secrets.token_hex(16), "region": "cn-north-1" if engine == "volc" else "cn-hangzhou"}


class LocalFiles(unittest.TestCase):
    def setUp(self):
        clean_environment = {key: value for key, value in os.environ.items() if not key.startswith("MT_")}
        environment = patch.dict(os.environ, clean_environment, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
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
        query = parse_query("x:123", {"x": "extra"}, "de")
        self.assertEqual((query.engine, query.request.to_lang), ("extra", "de"))

    def test_automatic_chinese_and_english_targets(self):
        for text, target in (("你好", "en"), ("繁體中文", "en"), ("𠀀", "en"),
                             ("Hello, world!", "zh"), ("Ｈｅｌｌｏ", "zh"),
                             ("Python 教程 3.10 😀", "en"), ("hello 123 😀", "zh")):
            with self.subTest(text=text):
                request = parse_query(text, self.aliases, "fr").request
                self.assertEqual((request.text, request.to_lang, request.from_lang), (text, target, "auto"))

    def test_explicit_target_overrides_automatic_direction(self):
        for prefix in ("", "v:", "b:", "a:", "m:", "all:"):
            for target, text in (("zh", "你好"), ("en", "hello"), ("ja", "你好 hello")):
                with self.subTest(prefix=prefix, target=target):
                    request = parse_query(prefix + f"set:{target} {text}", self.aliases, "de").request
                    self.assertEqual((request.text, request.to_lang), (text, target))

    def test_direction_uses_body_after_prefix_and_preserves_whitespace(self):
        for prefix in ("", "v:", "b:", "a:", "m:", "all:"):
            request = parse_query(prefix + " 你好\nworld ", self.aliases).request
            self.assertEqual((request.text, request.to_lang), (" 你好\nworld ", "en"))
        self.assertEqual(parse_query("-- set:en hello", self.aliases).request.to_lang, "zh")
        self.assertEqual(parse_query("v:-- set:zh 你好", self.aliases).request.to_lang, "en")

    def test_other_scripts_and_no_letters_use_configured_default(self):
        for text in ("こんにちは", "漢字かな English", "안녕하세요", "Привет", "hello русский",
                     "123 + 456 😀", "", " \n "):
            with self.subTest(text=text):
                self.assertEqual(parse_query(text, self.aliases, "fr").request.to_lang, "fr")


class ConfigTests(LocalFiles):
    def test_youdao_defaults_and_existing_json_compatibility(self):
        self.assertEqual(self.config["engines"]["youdao"],
                         {"enable": False, "app_key": "", "app_secret": "", "proxy_mode": "inherit", "proxy_url": ""})
        del self.config["engines"]["youdao"]
        self.config["fallback_order"].remove("youdao")
        path = self.path / "data" / "config.json"
        original = json.dumps(self.config).encode()
        path.write_bytes(original)
        self.assertNotIn("youdao", load_config(path.parent)["engines"])
        self.assertEqual(path.read_bytes(), original)

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


class PanelConfigTests(LocalFiles):
    def setUp(self):
        super().setUp()
        self.metadata = plistlib.loads((ROOT / "info.plist").read_bytes())

    def panel_environment(self, **overrides):
        values = {}
        for field in self.metadata["userconfigurationconfig"]:
            default = field["config"]["default"]
            values[field["variable"]] = ("1" if default else "0") if type(default) is bool else str(default)
        values["MT_USE_PANEL"] = "1"
        values.update(overrides)
        return values

    def test_native_defaults_match_fields_without_creating_json(self):
        directory = self.path / "native-only"
        with patch.dict(os.environ, self.panel_environment()):
            config = load_config(directory)
        self.assertFalse(directory.exists())
        self.assertEqual(config["default_engine"], "volc")
        self.assertTrue(config["fallback_enable"])
        self.assertFalse(any(value["enable"] for value in config["engines"].values()))
        self.assertEqual(config["engines"]["volc"]["region"], "cn-north-1")
        self.assertEqual(config["engines"]["aliyun"]["region"], "cn-hangzhou")
        self.assertEqual(config["engines"]["azure"]["region"], "")

    def test_every_provider_field_is_read_and_trimmed(self):
        environment = self.panel_environment(MT_DEFAULT_ENGINE="aliyun", MT_FALLBACK_ENABLED="0",
                                             MT_FALLBACK_ORDER=" aliyun, baidu, azure, volc ")
        expected = {}
        for engine, values in self.config["engines"].items():
            environment[f"MT_{engine.upper()}_ENABLED"] = "1"
            expected[engine] = {"enable": True}
            for field in values:
                if field != "enable":
                    value = "direct" if field == "proxy_mode" else secrets.token_hex(8)
                    environment[f"MT_{engine.upper()}_{field.upper()}"] = " " + value + " "
                    expected[engine][field] = value
        with patch.dict(os.environ, environment):
            config = load_config(self.path / "data")
        self.assertEqual(config["engines"], expected)
        self.assertEqual(config["default_engine"], "aliyun")
        self.assertFalse(config["fallback_enable"])
        self.assertEqual(config["fallback_order"], ["aliyun", "baidu", "azure", "volc"])

    def test_zero_checkbox_is_disabled(self):
        with patch.dict(os.environ, self.panel_environment(MT_AZURE_ENABLED="0", MT_VOLC_ENABLED="1")):
            config = load_config(self.path / "data")
        self.assertFalse(config["engines"]["azure"]["enable"])
        self.assertTrue(config["engines"]["volc"]["enable"])

    def test_json_mode_ignores_panel_values_and_is_not_overwritten(self):
        path = self.path / "data" / "config.json"
        self.config["default_engine"] = "baidu"
        self.config["engines"]["baidu"] = settings("baidu")
        before = json.dumps(self.config).encode()
        path.write_bytes(before)
        with patch.dict(os.environ, self.panel_environment(MT_USE_PANEL="0", MT_DEFAULT_ENGINE="aliyun",
                                                           MT_BAIDU_ENABLED="invalid")):
            config = load_config(path.parent)
        self.assertEqual(config["default_engine"], "baidu")
        self.assertEqual(config["engines"]["baidu"]["appkey"], self.config["engines"]["baidu"]["appkey"])
        self.assertEqual(path.read_bytes(), before)

    def test_bad_json_does_not_block_panel_mode(self):
        path = self.path / "data" / "config.json"
        path.write_text("invalid json", encoding="utf-8")
        with patch.dict(os.environ, self.panel_environment()):
            self.assertEqual(load_config(path.parent)["default_engine"], "volc")
        self.assertEqual(path.read_text(), "invalid json")

    def test_cleared_panel_key_does_not_reuse_json_credentials(self):
        path = self.path / "data" / "config.json"
        self.config["engines"]["aliyun"] = settings("aliyun")
        before = json.dumps(self.config).encode()
        path.write_bytes(before)
        environment = self.panel_environment(MT_DEFAULT_ENGINE="aliyun", MT_ALIYUN_ENABLED="1",
                                             MT_ALIYUN_AK=secrets.token_hex(8), MT_ALIYUN_SK="")
        with patch.dict(os.environ, environment):
            config = load_config(path.parent)
        self.assertEqual(config["engines"]["aliyun"]["sk"], "")
        self.assertEqual(Aliyun(config["engines"]["aliyun"]).validate(TranslationRequest("hello")).error_code, "config")
        self.assertEqual(path.read_bytes(), before)

    def test_panel_validation_rejects_unknown_values(self):
        for name, value in (("MT_USE_PANEL", "false"), ("MT_FALLBACK_ENABLED", "yes"),
                            ("MT_AZURE_ENABLED", "True"), ("MT_DEFAULT_ENGINE", "unknown"),
                            ("MT_FALLBACK_ORDER", "azure,unknown")):
            with self.subTest(name=name), patch.dict(os.environ, self.panel_environment(**{name: value})):
                with self.assertRaises(ConfigError):
                    load_config(self.path / "data")

    def test_empty_fallback_order_is_allowed(self):
        with patch.dict(os.environ, self.panel_environment(MT_FALLBACK_ORDER="")):
            self.assertEqual(load_config(self.path / "data")["fallback_order"], [])

    def test_form_fields_and_private_defaults(self):
        fields = self.metadata["userconfigurationconfig"]
        variables = [field["variable"] for field in fields]
        self.assertEqual(len(variables), len(set(variables)))
        for field in fields:
            self.assertIn(field["type"], ("textfield", "checkbox", "popupbutton"))
            self.assertIn(field["variable"], self.metadata["variablesdontexport"])
            if field["type"] == "checkbox":
                self.assertIs(type(field["config"]["default"]), bool)
            elif field["type"] == "textfield":
                self.assertFalse(field["config"]["required"])
            else:
                self.assertIn(field["config"]["default"], [pair[1] for pair in field["config"]["pairs"]])
        private = [field for field in fields if field["variable"].endswith(("_AK", "_SK", "_KEY", "_APPID", "_APPKEY", "_APP_SECRET"))]
        self.assertEqual(len(private), 9)
        self.assertTrue(all(field["config"]["default"] == "" for field in private))
        self.assertIn("prefs.plist", (ROOT / ".gitignore").read_text().splitlines())

    def test_builder_rejects_embedded_panel_secret(self):
        metadata = self.metadata
        next(field for field in metadata["userconfigurationconfig"] if field["variable"] == "MT_AZURE_KEY")["config"]["default"] = secrets.token_hex(8)
        with patch("build_workflow.plistlib.loads", return_value=metadata):
            with self.assertRaisesRegex(ValueError, "不得预填"):
                build()

    def test_youdao_is_selectable_and_private_fields_are_bound(self):
        fields = {field["variable"]: field for field in self.metadata["userconfigurationconfig"]}
        self.assertIn(["有道翻译", "youdao"], fields["MT_DEFAULT_ENGINE"]["config"]["pairs"])
        environment = self.panel_environment(MT_DEFAULT_ENGINE="youdao", MT_YOUDAO_ENABLED="1",
                                             MT_YOUDAO_APP_KEY=" fixture-id ", MT_YOUDAO_APP_SECRET=" fixture-secret ")
        with patch.dict(os.environ, environment):
            config = load_config(self.path / "data")
        self.assertEqual(config["engines"]["youdao"],
                         {"enable": True, "app_key": "fixture-id", "app_secret": "fixture-secret",
                          "proxy_mode": "inherit", "proxy_url": ""})
        self.assertEqual(config["default_engine"], "youdao")

    def test_builder_rejects_youdao_template_credentials(self):
        for key in ("app_key", "app_secret"):
            with self.subTest(key=key):
                self.config["engines"]["youdao"][key] = secrets.token_hex(10)
                with patch("build_workflow.json.loads", return_value=self.config):
                    with self.assertRaisesRegex(ValueError, "不得包含凭据"):
                        build()
                self.config["engines"]["youdao"][key] = ""


class PanelImportTests(LocalFiles):
    def setUp(self):
        super().setUp()
        self.data = self.path / "data"
        self.source = self.data / "config.json"
        self.panel = self.path / "panel-data"
        self.panel.mkdir()
        self.preferences = self.panel / "prefs.plist"
        reloader = patch("panel_import._reload_workflow")
        self.reload = reloader.start()
        self.addCleanup(reloader.stop)

    def save_source(self):
        self.source.write_text(json.dumps(self.config, ensure_ascii=False), encoding="utf-8")
        return self.source.read_bytes()

    def test_import_round_trip_all_fields_without_changing_json_or_mode(self):
        self.config["default_engine"] = "aliyun"
        self.config["fallback_enable"] = False
        self.config["fallback_order"] = ["aliyun", "azure"]
        for i, values in enumerate(self.config["engines"].values()):
            values["enable"] = i % 2 == 0
            for field in values:
                if field != "enable":
                    values[field] = ("direct" if field == "proxy_mode" else
                                     secrets.token_hex(10) + '\n特殊字符 " \\ & $()')
        original = self.save_source()
        with patch.dict(os.environ, {"MT_USE_PANEL": "1", "MT_DEFAULT_ENGINE": "azure"}):
            self.assertTrue(import_json_to_panel(self.data, self.panel))
        saved = plistlib.loads(self.preferences.read_bytes())
        self.assertNotIn("MT_USE_PANEL", saved)
        self.assertNotIn("PYTHON_BINARY", saved)
        environment = {key: ("1" if value else "0") if type(value) is bool else value
                       for key, value in saved.items()}
        with patch.dict(os.environ, {**environment, "MT_USE_PANEL": "1"}):
            loaded = load_config(self.data)
        for key in ("default_engine", "fallback_enable", "fallback_order", "engines", "proxy"):
            self.assertEqual(loaded[key], self.config[key])
        self.assertEqual(self.source.read_bytes(), original)
        self.assertEqual((self.data / MARKER).read_bytes(), MARKER_CONTENT)
        self.assertEqual(self.preferences.stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.data / MARKER).stat().st_mode & 0o777, 0o600)
        self.reload.assert_called_once_with()

    def test_saved_values_including_empty_and_false_are_preserved(self):
        existing = {"MT_USE_PANEL": False, "MT_DEFAULT_ENGINE": "baidu", "MT_AZURE_KEY": "",
                    "MT_ALIYUN_ENABLED": False, "PYTHON_BINARY": "fixture-python",
                    "unrelated": {"nested": 7}}
        self.preferences.write_bytes(plistlib.dumps(existing))
        self.config["engines"]["azure"]["key"] = secrets.token_hex(8)
        self.save_source()
        self.assertTrue(import_json_to_panel(self.data, self.panel))
        saved = plistlib.loads(self.preferences.read_bytes())
        for key, value in existing.items():
            self.assertEqual(saved[key], value)
        self.assertEqual(saved["MT_VOLC_REGION"], "cn-north-1")

    def test_deleted_engine_stays_disabled_and_keeps_native_field_defaults(self):
        self.config["engines"].pop("volc")
        self.save_source()
        self.assertTrue(import_json_to_panel(self.data, self.panel))
        saved = plistlib.loads(self.preferences.read_bytes())
        self.assertIs(saved["MT_VOLC_ENABLED"], False)
        self.assertNotIn("MT_VOLC_REGION", saved)

    def test_completed_import_does_not_refill_deleted_values_or_read_bad_json(self):
        import_json_to_panel(self.data, self.panel)
        saved = plistlib.loads(self.preferences.read_bytes())
        del saved["MT_AZURE_KEY"]
        self.preferences.write_bytes(plistlib.dumps(saved))
        original = self.preferences.read_bytes()
        self.source.write_text("invalid json", encoding="utf-8")
        self.reload.reset_mock()
        self.assertFalse(import_json_to_panel(self.data, self.panel))
        self.assertEqual(self.preferences.read_bytes(), original)
        self.reload.assert_not_called()

    def test_missing_source_creates_nothing(self):
        missing = self.path / "no-data"
        self.assertFalse(import_json_to_panel(missing, self.panel))
        self.assertFalse(missing.exists())
        self.assertFalse(self.preferences.exists())
        self.reload.assert_not_called()

    def test_invalid_source_and_unsupported_engine_leave_preferences_untouched(self):
        original = plistlib.dumps({"MT_USE_PANEL": True})
        self.preferences.write_bytes(original)
        for contents in ('broken', '{"engines": []}', '{"fallback_enable": "false"}',
                         '{"default_engine":"extra"}', '{"engines":{"extra":{"enable":true}}}'):
            with self.subTest(contents=contents):
                self.source.write_text(contents, encoding="utf-8")
                with self.assertRaises(ConfigError):
                    import_json_to_panel(self.data, self.panel)
                self.assertEqual(self.preferences.read_bytes(), original)
                self.assertFalse((self.data / MARKER).exists())
        self.reload.assert_not_called()

    def test_bad_preferences_are_not_replaced(self):
        for original in (b"bad plist", b"<plist>", plistlib.dumps(["not", "a dict"])):
            with self.subTest(original=original):
                self.preferences.write_bytes(original)
                with self.assertRaises(ConfigError):
                    import_json_to_panel(self.data, self.panel)
                self.assertEqual(self.preferences.read_bytes(), original)
                self.assertFalse((self.data / MARKER).exists())
        self.reload.assert_not_called()

    def test_symlink_preferences_are_not_followed(self):
        target = self.panel / "untouched.plist"
        original = plistlib.dumps({"MT_DEFAULT_ENGINE": "baidu"})
        target.write_bytes(original)
        self.preferences.symlink_to(target.name)
        with self.assertRaisesRegex(ConfigError, "符号链接"):
            import_json_to_panel(self.data, self.panel)
        self.assertEqual(target.read_bytes(), original)
        self.reload.assert_not_called()

    def test_busy_import_lock_does_not_change_files(self):
        with (self.data / LOCK).open("wb") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(ConfigError, "尚未完成"):
                import_json_to_panel(self.data, self.panel)
        self.assertFalse(self.preferences.exists())
        self.assertFalse((self.data / MARKER).exists())

    def test_atomic_replace_failure_retains_original_and_cleans_temporary_data(self):
        original = plistlib.dumps({"MT_DEFAULT_ENGINE": "baidu"})
        self.preferences.write_bytes(original)
        with patch("panel_import.os.replace", side_effect=OSError("fixture failure")):
            with self.assertRaises(ConfigError):
                import_json_to_panel(self.data, self.panel)
        self.assertEqual(self.preferences.read_bytes(), original)
        self.assertEqual(list(self.path.rglob(".panel-import-*")), [])
        self.assertFalse((self.data / MARKER).exists())
        self.reload.assert_not_called()

    def test_reload_failure_restores_exact_bytes_or_absence(self):
        for original in (plistlib.dumps({"MT_DEFAULT_ENGINE": "baidu"}, fmt=plistlib.FMT_BINARY), None):
            with self.subTest(existed=original is not None):
                if original is not None:
                    self.preferences.write_bytes(original)
                    self.preferences.chmod(0o640)
                else:
                    self.preferences.unlink(missing_ok=True)
                self.reload.reset_mock()
                self.reload.side_effect = [subprocess.TimeoutExpired("fixture", 10), None]
                with self.assertRaisesRegex(ConfigError, "面板已恢复"):
                    import_json_to_panel(self.data, self.panel)
                if original is not None:
                    self.assertEqual(self.preferences.read_bytes(), original)
                    self.assertEqual(self.preferences.stat().st_mode & 0o777, 0o640)
                else:
                    self.assertFalse(self.preferences.exists())
                self.assertFalse((self.data / MARKER).exists())
                self.assertEqual(self.reload.call_count, 2)

    def test_marker_failure_restores_preferences(self):
        original = plistlib.dumps({"MT_USE_PANEL": False})
        self.preferences.write_bytes(original)
        def write(path, content, mode=0o600):
            if path.name == MARKER:
                raise PermissionError("fixture marker failure")
            return _atomic_write(path, content, mode)
        with patch("panel_import._atomic_write", side_effect=write):
            with self.assertRaisesRegex(ConfigError, "面板已恢复"):
                import_json_to_panel(self.data, self.panel)
        self.assertEqual(self.preferences.read_bytes(), original)
        self.assertFalse((self.data / MARKER).exists())
        self.assertEqual(self.reload.call_count, 2)

    def test_rollback_failure_is_reported_and_source_is_preserved(self):
        self.preferences.write_bytes(plistlib.dumps({"MT_USE_PANEL": False}))
        source = self.source.read_bytes()
        calls = []
        def write(path, content, mode=0o600):
            calls.append(path)
            if len(calls) == 2:
                raise PermissionError("fixture rollback failure")
            return _atomic_write(path, content, mode)
        self.reload.side_effect = subprocess.TimeoutExpired("fixture", 10)
        with patch("panel_import._atomic_write", side_effect=write):
            with self.assertRaisesRegex(ConfigError, "恢复未完成"):
                import_json_to_panel(self.data, self.panel)
        self.assertEqual(self.source.read_bytes(), source)
        self.assertFalse((self.data / MARKER).exists())

    def test_concurrent_change_before_replace_is_preserved(self):
        changed = plistlib.dumps({"MT_DEFAULT_ENGINE": "azure"})
        reads = []
        def read(path):
            reads.append(path)
            if len(reads) == 2:
                self.preferences.write_bytes(changed)
            return _read_preferences(path)
        with patch("panel_import._read_preferences", side_effect=read):
            with self.assertRaisesRegex(ConfigError, "正在被修改"):
                import_json_to_panel(self.data, self.panel)
        self.assertEqual(self.preferences.read_bytes(), changed)
        self.reload.assert_not_called()

    def test_concurrent_change_after_reload_is_not_rolled_back(self):
        changed = plistlib.dumps({"MT_DEFAULT_ENGINE": "azure"})
        self.reload.side_effect = lambda: self.preferences.write_bytes(changed)
        with self.assertRaisesRegex(ConfigError, "未覆盖新设置"):
            import_json_to_panel(self.data, self.panel)
        self.assertEqual(self.preferences.read_bytes(), changed)
        self.assertFalse((self.data / MARKER).exists())
        self.reload.assert_called_once_with()

    def test_invalid_marker_prevents_reimport(self):
        (self.data / MARKER).write_text("invalid marker", encoding="utf-8")
        with self.assertRaisesRegex(ConfigError, "导入记录"):
            import_json_to_panel(self.data, self.panel)
        self.assertFalse(self.preferences.exists())
        self.reload.assert_not_called()

    def test_existing_import_marker_is_preserved_after_adding_youdao(self):
        (self.data / MARKER).write_bytes(MARKER_CONTENT)
        self.config["engines"]["youdao"] = {"enable": True, "app_key": secrets.token_hex(10),
                                          "app_secret": secrets.token_hex(10)}
        source = self.save_source()
        self.assertFalse(import_json_to_panel(self.data, self.panel))
        self.assertEqual((self.data / MARKER).read_bytes(), MARKER_CONTENT)
        self.assertEqual(self.source.read_bytes(), source)
        self.assertFalse(self.preferences.exists())
        self.reload.assert_not_called()

    def test_reload_command_uses_only_fixed_bundle_identifier(self):
        with patch("panel_import.subprocess.run") as process:
            _reload_workflow()
        command = process.call_args.args[0]
        self.assertEqual(command, ["osascript", "-e", f'tell application id "com.runningwithcrayons.Alfred" to reload workflow "{BUNDLE_ID}"'])
        self.assertEqual(process.call_args.kwargs, {"check": True, "capture_output": True, "timeout": 10})


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.request = TranslationRequest("你好 &+\n世界", "en")

    def test_discovery(self):
        adapters, errors = discover()
        self.assertEqual(set(adapters), {"volc", "baidu", "aliyun", "azure", "youdao"})
        self.assertEqual(errors, [])

    def test_no_credentials_never_contacts_transport(self):
        for adapter in (Volc, Baidu, Aliyun, Azure, Youdao):
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
        for cls in (Volc, Aliyun, Youdao):
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

    def test_youdao_v3_signature_uses_original_text_and_unicode_character_count(self):
        config = settings("youdao")
        adapter = Youdao(config)
        cases = [("你好 +&\n𠀀😀", "你好 +&\n𠀀😀"),
                 ("0123456789abcdefghij", "0123456789abcdefghij"),
                 ("0123456789Xabcdefghij", "012345678921abcdefghij"),
                 ("😀" * 10 + "中" + "𠀀" * 10, "😀" * 10 + "21" + "𠀀" * 10)]
        for text, signature_text in cases:
            with self.subTest(text=text), patch("adapters.youdao.uuid4", return_value="fixture-nonce"), \
                    patch("adapters.youdao.time.time", return_value=1700000000.75):
                wire = adapter.build_request(TranslationRequest(text, "zh"))
            body = parse_qs(wire.data.decode())
            expected = hashlib.sha256((config["app_key"] + signature_text + "fixture-nonce" +
                                       "1700000000" + config["app_secret"]).encode()).hexdigest()
            self.assertEqual(wire.full_url, "https://openapi.youdao.com/api")
            self.assertEqual(wire.get_method(), "POST")
            self.assertEqual(body, {"q": [text], "from": ["auto"], "to": ["zh-CHS"],
                                    "appKey": [config["app_key"]], "salt": ["fixture-nonce"],
                                    "curtime": ["1700000000"], "signType": ["v3"],
                                    "sign": [expected], "strict": ["true"]})
            self.assertNotIn(config["app_secret"], wire.data.decode())
            self.assertIn("application/x-www-form-urlencoded", wire.get_header("Content-type"))

    def test_youdao_languages_and_nonce(self):
        adapter = Youdao(settings("youdao"))
        for language, vendor in (("zh", "zh-CHS"), ("en", "en"), ("ja", "ja"),
                                  ("ko", "ko"), ("fr", "fr"), ("de", "de"), ("ru", "ru")):
            with self.subTest(language=language):
                body = parse_qs(adapter.build_request(TranslationRequest("hello", language, language)).data.decode())
                self.assertEqual((body["from"], body["to"], body["strict"]), ([vendor], [vendor], ["true"]))
        one = parse_qs(adapter.build_request(self.request).data.decode())
        two = parse_qs(adapter.build_request(self.request).data.decode())
        self.assertNotEqual(one["salt"], two["salt"])

    def test_youdao_translation_and_detected_language(self):
        adapter = Youdao(settings("youdao"))
        for detected, expected in (("EN", "en"), ("en", "en"), ("zh-CHS", "zh"),
                                   ("JA", "ja"), ("unknown", "und")):
            payload = {"errorCode": "0", "translation": ["第一段\n原换行", "第二段"], "l": detected + "2en"}
            result = adapter.parse_response(self.request, HttpResponse(200, payload, {}))
            self.assertEqual((result.success, result.translated_text, result.from_lang),
                             (True, "第一段\n原换行\n第二段", expected))
        result = adapter.parse_response(TranslationRequest("hi", "zh", "fr"),
                                        HttpResponse(200, {"errorCode": "0", "translation": ["你好"]}, {}))
        self.assertEqual(result.from_lang, "fr")

    def test_youdao_errors_and_safe_diagnostics(self):
        adapter = Youdao(settings("youdao"))
        cases = {"101": "invalid_input", "102": "unsupported_language", "103": "input_too_long",
                 "108": "auth", "110": "auth", "111": "auth", "202": "auth", "203": "auth",
                 "205": "auth", "206": "auth", "207": "auth", "302": "service_unavailable",
                 "303": "service_unavailable", "304": "service_unavailable", "401": "quota",
                 "411": "rate_limit", "412": "rate_limit", "999": "unknown"}
        private = secrets.token_hex(12)
        for code, expected in cases.items():
            with self.subTest(code=code):
                result = adapter.parse_response(self.request, HttpResponse(200, {"errorCode": code, "message": private}, {}))
                self.assertEqual((result.error_code, result.vendor_code), (expected, code))
                self.assertNotIn(private, json.dumps(result.to_dict()))
        for status, expected in ((401, "auth"), (429, "rate_limit"), (503, "service_unavailable")):
            self.assertEqual(adapter.parse_response(self.request, HttpResponse(status, None, {})).error_code, expected)

    def test_youdao_invalid_success_payloads_never_report_success(self):
        payloads = [None, {"translation": ["missing status"]}, {"errorCode": "0"},
                    {"errorCode": "0", "translation": []}, {"errorCode": "0", "translation": "text"},
                    {"errorCode": "0", "translation": [12]}, {"errorCode": "0", "translation": [" "]},
                    {"errorCode": "0", "translation": ["text"], "l": []}]
        for payload in payloads:
            with self.subTest(payload=payload):
                adapter = Youdao(settings("youdao"), lambda *_: HttpResponse(200, payload, {}))
                self.assertEqual(adapter.translate(self.request, 1).error_code, "unknown")

    def test_youdao_preflight_and_timeout_never_leak_credentials(self):
        for values, text, expected in (({"enable": False}, "hi", "disabled"),
                                       ({"enable": True}, "hi", "config"),
                                       (settings("youdao"), "x" * 5001, "input_too_long")):
            calls = []
            adapter = Youdao(values, lambda *_: calls.append(True))
            self.assertEqual(adapter.translate(TranslationRequest(text), 1).error_code, expected)
            self.assertEqual(calls, [])
        def timeout(*_):
            raise TransportError("timeout")
        self.assertEqual(Youdao(settings("youdao"), timeout).translate(self.request, 1).error_code, "network")

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


class AutomaticTranslationTests(LocalFiles):
    """解析、真实适配器签名与调度串联；只替换 HTTP 边界。"""

    def setUp(self):
        super().setUp()
        self.config["cache_enabled"] = False
        self.config["engines"]["youdao"]["enable"] = True
        classes, _ = discover()
        self.aliases = {cls.prefix: name for name, cls in classes.items()}
        self.wires = []
        self.payloads = {
            "volc": {"TranslationList": [{"Translation": "translated"}]},
            "baidu": {"trans_result": [{"dst": "translated"}]},
            "aliyun": {"Code": 200, "Data": {"Translated": "translated"}},
            "azure": [{"translations": [{"text": "translated"}]}],
            "youdao": {"errorCode": "0", "translation": ["translated"]},
        }
        self.adapters = {}
        for engine, cls in classes.items():
            def transport(wire, timeout, engine=engine):
                self.wires.append((engine, wire))
                return HttpResponse(200, self.payloads[engine], {})
            self.adapters[engine] = cls(settings(engine), transport)

    def translate(self, text):
        query = parse_query(text, self.aliases, self.config["target_lang_default"])
        return Scheduler(self.config, self.adapters, self.path / "cache").translate(query)

    def wire_languages(self, engine, wire):
        if engine == "volc":
            body = json.loads(wire.data)
            return body["TargetLanguage"], body.get("SourceLanguage", "auto")
        if engine == "azure":
            query = parse_qs(urlsplit(wire.full_url).query)
            return query["to"][0], query.get("from", ["auto"])[0]
        body = parse_qs(wire.data.decode())
        if engine in ("baidu", "youdao"):
            return body["to"][0], body["from"][0]
        return body["TargetLanguage"][0], body["SourceLanguage"][0]

    def test_every_provider_sends_automatic_and_manual_target_once(self):
        for prefix in self.aliases:
            for text, target in (("你好", "en"), ("hello", "zh"), ("set:zh 你好", "zh"),
                                 ("set:en hello", "en"), ("set:ja 你好", "ja")):
                with self.subTest(prefix=prefix, text=text):
                    self.wires.clear()
                    result = self.translate(prefix + ":" + text)
                    self.assertEqual(len(self.wires), 1)
                    self.assertTrue(result[0].success)
                    engine, wire = self.wires[0]
                    expected = self.adapters[engine].language_map[target]
                    self.assertEqual(self.wire_languages(engine, wire), (expected, "auto"))
                    self.assertEqual(result[0].to_lang, target)

    def test_all_providers_receive_same_direction(self):
        for text, target in (("你好", "en"), ("hello", "zh"), ("set:ja hello", "ja")):
            with self.subTest(text=text):
                self.wires.clear()
                results = self.translate("all:" + text)
                self.assertEqual(len(self.wires), 5)
                self.assertEqual({row.to_lang for row in results}, {target})
                for engine, wire in self.wires:
                    self.assertEqual(self.wire_languages(engine, wire), (self.adapters[engine].language_map[target], "auto"))

    def test_default_fallback_keeps_automatic_target_without_detection_call(self):
        self.payloads["volc"] = {"ResponseMetadata": {"Error": {"Code": "-429"}}}
        results = self.translate("你好 world")
        self.assertEqual([(row.engine, row.to_lang, row.fallback_from) for row in results], [("azure", "en", "volc")])
        self.assertEqual([engine for engine, _ in self.wires], ["volc", "azure"])
        self.assertTrue(all(self.wire_languages(engine, wire)[0] == "en" for engine, wire in self.wires))

    def test_youdao_fallback_and_cache_are_connected(self):
        self.config["fallback_order"] = ["youdao"]
        self.config["cache_enabled"] = True
        self.payloads["volc"] = {"ResponseMetadata": {"Error": {"Code": "-429"}}}
        result = self.translate("你好")[0]
        self.assertEqual((result.engine, result.to_lang, result.fallback_from), ("youdao", "en", "volc"))
        self.assertEqual([engine for engine, _ in self.wires], ["volc", "youdao"])
        self.assertEqual(parse_qs(self.wires[-1][1].data.decode())["strict"], ["true"])
        cached = self.translate("y:你好")[0]
        self.assertTrue(cached.cached)
        self.assertEqual(len(self.wires), 2)
        manual = self.translate("y:set:zh 你好")[0]
        self.assertEqual((manual.to_lang, manual.cached), ("zh", False))
        self.assertEqual(len(self.wires), 3)

    def test_automatic_and_manual_targets_do_not_mix_old_cache(self):
        self.config["cache_enabled"] = True
        cache = TranslationCache(self.path / "cache", self.config)
        old_request = TranslationRequest("你好", "zh")
        cache.put(old_request, TranslationResult(True, "azure", "你好", "旧中文缓存", "zh", "zh"))
        automatic = self.translate("m:你好")[0]
        self.assertEqual((automatic.to_lang, automatic.cached), ("en", False))
        self.assertEqual(len(self.wires), 1)
        self.assertTrue(self.translate("m:你好")[0].cached)
        manual = self.translate("m:set:zh 你好")[0]
        self.assertEqual((manual.translated_text, manual.to_lang, manual.cached), ("旧中文缓存", "zh", True))
        self.assertEqual(len(self.wires), 1)


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
        self.config["engines"]["youdao"]["enable"] = True
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
            self.assertEqual({entered.get(timeout=1) for _ in self.adapters}, set(self.adapters))
        finally:
            release.set()
            thread.join(2)
        self.assertEqual([row.engine for row in output.get(timeout=1)], self.config["fallback_order"])

    def test_all_partial_failure_no_fallback(self):
        self.adapters["azure"].outcome = "rate_limit"
        result = self.scheduler().translate(Query(TranslationRequest("hello"), all_engines=True))
        self.assertEqual(sum(row.success for row in result), 3)
        self.assertEqual(len(self.calls), 4)
        self.assertNotIn("youdao", self.calls)

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
    def setUp(self):
        super().setUp()
        importer = patch("main.import_json_to_panel", return_value=False)
        self.importer = importer.start()
        self.addCleanup(importer.stop)
        window = patch("main.open_window", return_value="cancelled")
        self.window = window.start()
        self.addCleanup(window.stop)

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

    def test_configure_imports_before_opening_independent_window(self):
        order = []
        self.importer.side_effect = lambda directory: order.append("import") or True
        self.window.side_effect = lambda note: order.append("open") or "saved"
        with patch.dict(os.environ, self.environment()):
            result = run(["configure"])
        self.assertEqual(order, ["import", "open"])
        self.importer.assert_called_once_with(self.path / "data")
        self.assertIn("已从 JSON 导入", self.window.call_args.args[0])
        self.assertEqual(result["items"][0]["title"], "设置已保存")

    def test_failed_import_still_opens_panel_with_safe_reason(self):
        self.importer.side_effect = ConfigError("自动导入未完成，原 JSON 保留")
        with patch.dict(os.environ, self.environment()):
            result = run(["configure"])
        self.assertEqual(result["items"][0]["title"], "已关闭设置窗口")
        self.assertIn("原 JSON 保留", self.window.call_args.args[0])
        self.window.assert_called_once()

    def test_native_configuration_remains_available(self):
        with patch.dict(os.environ, self.environment()), patch("main.subprocess.run") as opened:
            run(["configure-native"])
        command = opened.call_args.args[0]
        self.assertEqual(command[:2], ["osascript", "-e"])
        self.assertIn(f'reveal workflow "{BUNDLE_ID}" configuration true', command[2])
        self.window.assert_not_called()

    def test_configure_json_only_opens_the_private_file(self):
        with patch.dict(os.environ, self.environment()), patch("main.subprocess.run") as opened:
            result = run(["configure-json"])
        self.assertEqual(opened.call_args.args[0], ["open", "-t", str(self.path / "data" / "config.json")])
        self.assertFalse(result["items"][0]["valid"])
        self.importer.assert_not_called()

    def test_configure_failure_returns_safe_guidance(self):
        with patch.dict(os.environ, self.environment()), patch("main.open_window", side_effect=ConfigError("请使用 trconfig-native 或 trconfig-json")):
            result = run(["configure"])
        self.assertFalse(result["items"][0]["valid"])
        self.assertIn("trconfig-native", result["items"][0]["subtitle"])

    def test_native_panel_applescript_compiles_without_execution(self):
        compiler = shutil.which("osacompile")
        if compiler is None:
            self.skipTest("当前系统没有 AppleScript 编译器")
        with patch.dict(os.environ, self.environment()), patch("main.subprocess.run") as opened:
            run(["configure-native"])
        scripts = [opened.call_args.args[0][2]]
        with patch("panel_import.subprocess.run") as reloaded:
            _reload_workflow()
        scripts.append(reloaded.call_args.args[0][2])
        for i, script in enumerate(scripts):
            process = subprocess.run([compiler, "-o", str(self.path / f"configure-{i}.scpt"), "-e", script],
                                     capture_output=True, text=True, timeout=10)
            self.assertEqual(process.returncode, 0, process.stderr)

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
