"""代理配置和本地 HTTP/HTTPS 链路验证；不访问真实翻译接口。"""
from __future__ import annotations

import base64
import json
import os
import plistlib
import secrets
import select
import shutil
import socket
import ssl
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote, urlsplit
from urllib.request import HTTPSHandler, Request, build_opener

from adapters.base import BaseAdapter
from build_workflow import build
from config import ROOT, ConfigError, load_config, resolve_proxy
from http_client import TransportError, send
from main import run
from panel_import import import_json_to_panel
from test_workflow import LocalFiles


class ProxyConfigTests(LocalFiles):
    def load_values(self, values):
        (self.path / "data" / "config.json").write_text(json.dumps(values), encoding="utf-8")
        return load_config(self.path / "data")

    def test_old_json_defaults_without_rewriting(self):
        self.config.pop("proxy")
        for settings in self.config["engines"].values():
            settings.pop("proxy_mode")
            settings.pop("proxy_url")
        content = json.dumps(self.config).encode()
        path = self.path / "data" / "config.json"
        path.write_bytes(content)
        loaded = load_config(path.parent)
        self.assertEqual(loaded["proxy"], {"mode": "system", "url": ""})
        self.assertTrue(all(resolve_proxy(loaded, settings) == ("system", "")
                            for settings in loaded["engines"].values()))
        self.assertEqual(path.read_bytes(), content)

    def test_global_and_provider_mode_matrix(self):
        for global_mode in ("system", "direct", "custom"):
            for provider_mode in ("inherit", "system", "direct", "custom"):
                with self.subTest(global_mode=global_mode, provider_mode=provider_mode):
                    self.config["proxy"] = {"mode": global_mode, "url": "http://global.invalid:8080"}
                    self.config["engines"]["volc"].update(proxy_mode=provider_mode, proxy_url="http://specific.invalid:9090")
                    loaded = self.load_values(self.config)
                    mode = global_mode if provider_mode == "inherit" else provider_mode
                    url = ("http://global.invalid:8080" if provider_mode == "inherit" else "http://specific.invalid:9090") if mode == "custom" else ""
                    self.assertEqual(resolve_proxy(loaded, loaded["engines"]["volc"]), (mode, url))

    def test_invalid_proxy_urls_are_safe_configuration_errors(self):
        secret = secrets.token_hex(12)
        invalid = ["", "127.0.0.1:7890", "socks5://proxy.invalid:1080", "https://proxy.invalid:443",
                   "http://", "http://proxy.invalid:0", "http://proxy.invalid:65536", "http://proxy.invalid:",
                   "http://proxy.invalid/path", "http://proxy.invalid?q=1", "http://proxy.invalid#fragment",
                   "http://bad host:8080", "http://proxy.invalid\r\nInjected: yes", "http://[bad:8080",
                   f"http://user:{secret}@proxy.invalid:bad"]
        for value in invalid:
            with self.subTest(value=value):
                self.config["proxy"] = {"mode": "custom", "url": value}
                with self.assertRaises(ConfigError) as failure:
                    self.load_values(self.config)
                self.assertNotIn(secret, str(failure.exception))
                self.assertNotIn("proxy.invalid", str(failure.exception))

    def test_proxy_modes_and_types_are_validated(self):
        for value in ([], {"mode": "inherit"}, {"mode": False}, {"url": 123}):
            with self.subTest(value=value), self.assertRaises(ConfigError):
                self.load_values({"proxy": value})
        for value in (None, "bad", "CUSTOM"):
            with self.subTest(value=value), self.assertRaises(ConfigError):
                self.load_values({"engines": {"youdao": {"proxy_mode": value}}})
        with self.assertRaises(ConfigError):
            self.load_values({"engines": {"youdao": {"proxy_mode": "custom", "proxy_url": ""}}})

    def test_valid_addresses_and_unused_text(self):
        for value in ("http://localhost", "http://127.0.0.1:7890/", "http://[::1]:8080",
                      "http://user%20name:pass%40word@proxy.invalid:8080"):
            with self.subTest(value=value):
                loaded = self.load_values({"proxy": {"mode": "custom", "url": value}})
                self.assertEqual(loaded["proxy"]["url"], value)
        loaded = self.load_values({"proxy": {"mode": "direct", "url": "unused text"}})
        self.assertEqual(resolve_proxy(loaded, {}), ("direct", ""))

    def test_panel_modes_bind_to_every_provider(self):
        metadata = plistlib.loads((ROOT / "info.plist").read_bytes())
        fields = {field["variable"]: field for field in metadata["userconfigurationconfig"]}
        self.assertEqual(len(fields), 34)
        environment = {"MT_USE_PANEL": "1", "MT_PROXY_MODE": "custom", "MT_PROXY_URL": "http://global.invalid:8080"}
        for name in self.config["engines"]:
            mode = f"MT_{name.upper()}_PROXY_MODE"
            url = f"MT_{name.upper()}_PROXY_URL"
            self.assertEqual(fields[mode]["config"]["default"], "inherit")
            self.assertEqual(fields[url]["config"]["default"], "")
            self.assertIn(mode, metadata["variablesdontexport"])
            self.assertIn(url, metadata["variablesdontexport"])
            self.assertEqual({pair[1] for pair in fields[mode]["config"]["pairs"]},
                             {"inherit", "system", "direct", "custom"})
            environment[mode] = "custom"
            environment[url] = " http://" + name + ".invalid:8080 "
        with patch.dict(os.environ, environment):
            loaded = load_config(self.path / "data")
        self.assertEqual(loaded["proxy"], {"mode": "custom", "url": "http://global.invalid:8080"})
        for name, settings in loaded["engines"].items():
            self.assertEqual(resolve_proxy(loaded, settings), ("custom", f"http://{name}.invalid:8080"))

    def test_proxy_addresses_are_excluded_from_release_defaults(self):
        for location in ("global", "provider"):
            values = json.loads(json.dumps(self.config))
            if location == "global":
                values["proxy"]["url"] = "http://private.invalid:8080"
            else:
                values["engines"]["volc"]["proxy_url"] = "http://private.invalid:8080"
            with patch("build_workflow.json.loads", return_value=values):
                with self.assertRaisesRegex(ValueError, "代理"):
                    build()
        metadata = plistlib.loads((ROOT / "info.plist").read_bytes())
        field = next(item for item in metadata["userconfigurationconfig"] if item["variable"] == "MT_PROXY_URL")
        field["config"]["default"] = "http://private.invalid:8080"
        with patch("build_workflow.plistlib.loads", return_value=metadata):
            with self.assertRaisesRegex(ValueError, "不得预填"):
                build()

    def test_json_import_preserves_saved_proxy_and_fills_missing_fields(self):
        self.config["proxy"] = {"mode": "custom", "url": "http://global.invalid:8080"}
        self.config["engines"]["azure"].update(proxy_mode="custom", proxy_url="http://azure.invalid:8080")
        source = json.dumps(self.config).encode()
        (self.path / "data" / "config.json").write_bytes(source)
        panel = self.path / "panel-values"
        panel.mkdir()
        (panel / "prefs.plist").write_bytes(plistlib.dumps({"MT_PROXY_MODE": "direct", "MT_VOLC_PROXY_MODE": "system"}))
        with patch("panel_import._reload_workflow"):
            self.assertTrue(import_json_to_panel(self.path / "data", panel))
        imported = plistlib.loads((panel / "prefs.plist").read_bytes())
        self.assertEqual(imported["MT_PROXY_MODE"], "direct")
        self.assertEqual(imported["MT_VOLC_PROXY_MODE"], "system")
        self.assertEqual(imported["MT_PROXY_URL"], "http://global.invalid:8080")
        self.assertEqual(imported["MT_AZURE_PROXY_URL"], "http://azure.invalid:8080")
        self.assertEqual(imported["MT_AZURE_PROXY_MODE"], "custom")
        self.assertEqual((self.path / "data" / "config.json").read_bytes(), source)


def recording_server(label):
    """HTTP 请求记录路线；CONNECT 只转发到本机测试源站。"""
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            self.server.records.append((self.command, self.path, dict(self.headers), body))
            if urlsplit(self.path).path == "/redirect":
                self.send_response(302)
                self.send_header("Location", "/must-not-follow")
                self.end_headers()
                return
            data = json.dumps({"route": label}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        do_POST = do_GET

        def do_CONNECT(self):
            self.server.records.append(("CONNECT", self.path, dict(self.headers), b""))
            destination = urlsplit("//" + self.path)
            if destination.hostname not in ("localhost", "127.0.0.1"):
                self.send_error(403)
                return
            try:
                with socket.create_connection((destination.hostname, destination.port), timeout=2) as upstream:
                    self.send_response(200, "Connection established")
                    self.end_headers()
                    self.wfile.flush()
                    sockets = [self.connection, upstream]
                    while True:
                        ready, _, _ = select.select(sockets, [], [], 3)
                        if not ready:
                            return
                        for source in ready:
                            data = source.recv(65536)
                            if not data:
                                return
                            target = upstream if source is self.connection else self.connection
                            target.sendall(data)
            except OSError:
                return

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    server.records = []
    return server


class ProxyNetworkTests(LocalFiles):
    def setUp(self):
        super().setUp()
        clean = {key: value for key, value in os.environ.items() if not key.lower().endswith("_proxy")}
        environment = patch.dict(os.environ, clean, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        self.origin = self.start_server("origin")
        self.proxy_a = self.start_server("proxy-a")
        self.proxy_b = self.start_server("proxy-b")

    def start_server(self, label, context=None):
        server = recording_server(label)
        if context:
            server.socket = context.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        thread.start()
        def stop():
            server.shutdown()
            server.server_close()
            thread.join(2)
        self.addCleanup(stop)
        return server

    @staticmethod
    def address(server):
        return f"http://127.0.0.1:{server.server_port}"

    def test_system_direct_and_custom_route_without_environment_mutation(self):
        target = self.address(self.origin) + "/translate"
        with patch.dict(os.environ, {"http_proxy": self.address(self.proxy_a), "no_proxy": ""}):
            before = dict(os.environ)
            self.assertEqual(send(Request(target), 2).payload["route"], "proxy-a")
            self.assertEqual(send(Request(target), 2, proxy_mode="direct").payload["route"], "origin")
            with patch.dict(os.environ, {"no_proxy": "*"}):
                self.assertEqual(send(Request(target), 2).payload["route"], "origin")
                self.assertEqual(send(Request(target), 2, proxy_mode="custom", proxy_url=self.address(self.proxy_b)).payload["route"], "proxy-b")
            self.assertEqual(dict(os.environ), before)

    def test_custom_proxy_failure_does_not_connect_directly(self):
        with socket.socket() as closed_port:
            closed_port.bind(("127.0.0.1", 0))
            unavailable = f"http://127.0.0.1:{closed_port.getsockname()[1]}"
            with self.assertRaises(TransportError):
                send(Request(self.address(self.origin)), 1, proxy_mode="custom", proxy_url=unavailable)
        self.assertEqual(self.origin.records, [])

    def test_custom_proxy_keeps_redirect_blocking(self):
        response = send(Request(self.address(self.origin) + "/redirect"), 2,
                        proxy_mode="custom", proxy_url=self.address(self.proxy_a))
        self.assertEqual(response.status, 302)
        self.assertEqual(len(self.proxy_a.records), 1)
        self.assertEqual(self.origin.records, [])

    def configure_entry(self, overrides=None):
        self.config["cache_enabled"] = False
        self.config["proxy"] = {"mode": "custom", "url": self.address(self.proxy_a)}
        modes = {"volc": ("inherit", ""), "baidu": ("direct", ""),
                 "aliyun": ("custom", self.address(self.proxy_b)), "azure": ("system", ""),
                 "youdao": ("inherit", "")}
        classes = {}
        aliases = {"volc": "v", "baidu": "b", "aliyun": "a", "azure": "m", "youdao": "y"}
        target = self.address(self.origin)
        for engine, (mode, url) in modes.items():
            self.config["engines"][engine].update(enable=True, proxy_mode=mode, proxy_url=url)
            def make_adapter(name):
                class Adapter(BaseAdapter):
                    engine, prefix = name, aliases[name]
                    language_map = {"zh": "zh", "en": "en"}
                    def build_request(self, request):
                        return Request(target + "/" + name, data=request.text.encode())
                    def parse_response(self, request, response):
                        return self.success(request, response.payload["route"])
                Adapter.name = name
                return Adapter
            classes[engine] = make_adapter(engine)
        if overrides:
            overrides(self.config)
        (self.path / "data" / "config.json").write_text(json.dumps(self.config), encoding="utf-8")
        environment = {"alfred_workflow_data": str(self.path / "data"),
                       "alfred_workflow_cache": str(self.path / "cache"),
                       "http_proxy": self.address(self.proxy_b), "no_proxy": ""}
        return classes, environment

    def test_five_provider_concurrent_entry_uses_independent_routes(self):
        classes, environment = self.configure_entry()
        with patch.dict(os.environ, environment), patch("main.discover", return_value=(classes, [])):
            before = dict(os.environ)
            result = run(["translate", "all:hello"])
            self.assertEqual(dict(os.environ), before)
        self.assertEqual(len(result["items"]), 5)
        self.assertTrue(all(item["valid"] for item in result["items"]))
        paths = lambda server: {urlsplit(row[1]).path for row in server.records}
        self.assertEqual(paths(self.proxy_a), {"/volc", "/youdao"})
        self.assertEqual(paths(self.proxy_b), {"/aliyun", "/azure"})
        self.assertEqual(paths(self.origin), {"/baidu"})

    def test_fallback_uses_next_providers_own_route(self):
        with socket.socket() as unavailable:
            unavailable.bind(("127.0.0.1", 0))
            def override(config):
                config["engines"]["volc"].update(proxy_mode="custom", proxy_url=f"http://127.0.0.1:{unavailable.getsockname()[1]}")
                config["fallback_order"] = ["baidu"]
            classes, environment = self.configure_entry(override)
            with patch.dict(os.environ, environment), patch("main.discover", return_value=(classes, [])):
                result = run(["translate", "hello"])
        self.assertTrue(result["items"][0]["valid"])
        self.assertEqual(result["items"][0]["title"], "origin")
        self.assertEqual(self.proxy_a.records, [])
        self.assertEqual(self.proxy_b.records, [])

    def test_https_connect_authentication_and_tls_validation(self):
        openssl = shutil.which("openssl")
        if not openssl:
            self.skipTest("没有本机 OpenSSL，未验证本地 TLS 隧道")
        key, cert = self.path / "tls-key.pem", self.path / "tls-cert.pem"
        process = subprocess.run([openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                                  "-keyout", str(key), "-out", str(cert), "-days", "1",
                                  "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1"],
                                 capture_output=True, timeout=15)
        self.assertEqual(process.returncode, 0, "本地测试证书生成失败")
        server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server_context.load_cert_chain(cert, key)
        target = self.start_server("tls-origin", server_context)
        url = f"https://localhost:{target.server_port}/translate"
        with self.assertRaises(TransportError) as untrusted:
            send(Request(url), 2, proxy_mode="custom", proxy_url=self.address(self.proxy_a))
        self.assertIsInstance(getattr(untrusted.exception.__cause__, "reason", None), ssl.SSLCertVerificationError)
        self.assertEqual(target.records, [])
        trusted = ssl.create_default_context(cafile=str(cert))
        self.assertTrue(trusted.check_hostname)
        self.assertEqual(trusted.verify_mode, ssl.CERT_REQUIRED)
        user, password = "fixture user", secrets.token_hex(12)
        proxy_url = "http://" + quote(user) + ":" + password + "@" + urlsplit(self.address(self.proxy_a)).netloc
        def opener(*handlers):
            # 仅信任本测试证书，保留真实 TLS 校验；不修改系统证书库。
            return build_opener(*handlers, HTTPSHandler(context=trusted))
        with patch("http_client.build_opener", side_effect=opener):
            response = send(Request(url, data=b"hello", headers={"Authorization": "fixture-vendor"}), 2,
                            proxy_mode="custom", proxy_url=proxy_url)
        self.assertEqual(response.payload["route"], "tls-origin")
        connection = self.proxy_a.records[-1]
        self.assertEqual(connection[0], "CONNECT")
        expected = "Basic " + base64.b64encode((user + ":" + password).encode()).decode()
        self.assertEqual(connection[2]["Proxy-Authorization"], expected)
        origin_headers = target.records[-1][2]
        self.assertNotIn("Proxy-Authorization", origin_headers)
        self.assertEqual(origin_headers["Authorization"], "fixture-vendor")
        self.assertEqual(origin_headers["Host"], f"localhost:{target.server_port}")
