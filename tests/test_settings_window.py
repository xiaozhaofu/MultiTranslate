"""独立设置窗口的配置事务与本地管道验证，不使用真实用户配置。"""
from __future__ import annotations

import io
import fcntl
import json
import os
import plistlib
import secrets
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from config import ConfigError
from config import ROOT
from panel_import import LOCK, _atomic_write
from settings_window import main, open_window, read_window_state, save_window_values
from test_workflow import LocalFiles


class WindowStoreTests(LocalFiles):
    def setUp(self):
        super().setUp()
        self.prefs = self.path / "prefs.plist"
        self.environment = patch.dict(os.environ, {"PYTHON_BINARY": "", "alfred_workflow_data": str(self.path / "data")})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        reloader = patch("settings_window._reload_workflow")
        self.reload = reloader.start()
        self.addCleanup(reloader.stop)

    def request(self):
        state = read_window_state(self.prefs)
        return {"values": state["values"], "snapshot": state["snapshot"]}

    def test_load_preserves_saved_empty_values_and_groups(self):
        saved = {"MT_USE_PANEL": False, "MT_AZURE_KEY": "", "MT_YOUDAO_ENABLED": False,
                 "MT_PROXY_MODE": "direct", "unknown": {"value": "keep"}}
        self.prefs.write_bytes(plistlib.dumps(saved))
        with patch.dict(os.environ, {"MT_AZURE_KEY": secrets.token_hex(8), "MT_USE_PANEL": "1"}):
            state = read_window_state(self.prefs)
        self.assertEqual(len(state["fields"]), 34)
        self.assertEqual(len(state["groups"]), 8)
        self.assertEqual(state["values"]["MT_AZURE_KEY"], "")
        self.assertIs(state["values"]["MT_USE_PANEL"], False)
        self.assertIs(state["values"]["MT_YOUDAO_ENABLED"], False)
        self.assertEqual({v for group in state["groups"] for v in group["variables"]}, set(state["values"]))

    def test_save_preserves_unknown_keys_and_original_json(self):
        unknown = {"unrelated": {"binary": b"keep", "array": [1, 2]}}
        self.prefs.write_bytes(plistlib.dumps(unknown, fmt=plistlib.FMT_BINARY))
        source = (self.path / "data" / "config.json").read_bytes()
        request = self.request()
        secret = secrets.token_hex(16)
        request["values"].update(MT_USE_PANEL=True, MT_DEFAULT_ENGINE="youdao", MT_YOUDAO_APP_SECRET=secret)
        save_window_values(request, self.prefs)
        actual = plistlib.loads(self.prefs.read_bytes())
        self.assertEqual(actual["unrelated"], unknown["unrelated"])
        self.assertEqual(actual["MT_YOUDAO_APP_SECRET"], secret)
        self.assertIs(actual["MT_USE_PANEL"], True)
        self.assertEqual(self.prefs.stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.path / "data" / "config.json").read_bytes(), source)
        self.reload.assert_called_once_with()

    def test_bad_json_does_not_prevent_editing_window_settings(self):
        source = self.path / "data" / "config.json"
        source.write_text("broken JSON", encoding="utf-8")
        request = self.request()
        request["values"]["MT_USE_PANEL"] = True
        save_window_values(request, self.prefs)
        self.assertEqual(source.read_text(), "broken JSON")

    def test_invalid_values_leave_preferences_unchanged(self):
        original = plistlib.dumps({"MT_USE_PANEL": False})
        self.prefs.write_bytes(original)
        for change in ({"MT_PROXY_MODE": "custom", "MT_PROXY_URL": "socks5://private.invalid:8080"},
                       {"MT_DEFAULT_ENGINE": "unknown"}, {"MT_YOUDAO_ENABLED": "1"}):
            with self.subTest(change=change):
                request = self.request()
                request["values"].update(change)
                with self.assertRaises(ConfigError):
                    save_window_values(request, self.prefs)
                self.assertEqual(self.prefs.read_bytes(), original)
        self.reload.assert_not_called()

    def test_missing_and_extra_fields_are_rejected(self):
        for extra in (False, True):
            request = self.request()
            if extra:
                request["values"]["unrecognized"] = "value"
            else:
                del request["values"]["MT_PROXY_MODE"]
            with self.assertRaises(ConfigError):
                save_window_values(request, self.prefs)
        self.assertFalse(self.prefs.exists())

    def test_snapshot_conflict_does_not_overwrite_newer_configuration(self):
        request = self.request()
        newer = plistlib.dumps({"MT_DEFAULT_ENGINE": "azure"})
        self.prefs.write_bytes(newer)
        with self.assertRaisesRegex(ConfigError, "其他窗口"):
            save_window_values(request, self.prefs)
        self.assertEqual(self.prefs.read_bytes(), newer)
        self.reload.assert_not_called()

    def test_write_failure_keeps_original(self):
        original = plistlib.dumps({"MT_USE_PANEL": False})
        self.prefs.write_bytes(original)
        with patch("settings_window._atomic_write", side_effect=OSError("fixture")):
            with self.assertRaises(OSError):
                save_window_values(self.request(), self.prefs)
        self.assertEqual(self.prefs.read_bytes(), original)
        self.reload.assert_not_called()

    def test_reload_failure_restores_exact_bytes_or_absence(self):
        for original in (plistlib.dumps({"MT_USE_PANEL": True}, fmt=plistlib.FMT_BINARY), None):
            with self.subTest(existed=original is not None):
                if original is None:
                    self.prefs.unlink(missing_ok=True)
                else:
                    self.prefs.write_bytes(original)
                    self.prefs.chmod(0o640)
                self.reload.side_effect = [subprocess.TimeoutExpired("fixture", 10), None]
                with self.assertRaisesRegex(ConfigError, "原设置已恢复"):
                    save_window_values(self.request(), self.prefs)
                if original is None:
                    self.assertFalse(self.prefs.exists())
                else:
                    self.assertEqual(self.prefs.read_bytes(), original)
                    self.assertEqual(self.prefs.stat().st_mode & 0o777, 0o640)

    def test_rollback_failure_is_reported(self):
        self.prefs.write_bytes(plistlib.dumps({"MT_USE_PANEL": False}))
        request = self.request()
        writes = []
        def write(path, content, mode=0o600):
            writes.append(path)
            if len(writes) > 1:
                raise PermissionError("fixture")
            _atomic_write(path, content, mode)
        self.reload.side_effect = OSError("fixture")
        with patch("settings_window._atomic_write", side_effect=write):
            with self.assertRaisesRegex(ConfigError, "恢复未完成"):
                save_window_values(request, self.prefs)

    def test_concurrent_edit_after_write_is_not_rolled_back(self):
        request = self.request()
        newer = plistlib.dumps({"MT_DEFAULT_ENGINE": "azure"})
        self.reload.side_effect = lambda: self.prefs.write_bytes(newer)
        with self.assertRaisesRegex(ConfigError, "保留新值"):
            save_window_values(request, self.prefs)
        self.assertEqual(self.prefs.read_bytes(), newer)

    def test_helper_pipeline_saves_without_outputting_credentials(self):
        request = self.request()
        secret = secrets.token_hex(16)
        request["values"]["MT_AZURE_KEY"] = secret
        incoming = SimpleNamespace(buffer=io.BytesIO(json.dumps(request).encode()))
        outgoing = io.StringIO()
        with patch("sys.stdin", incoming), patch("sys.stdout", outgoing):
            self.assertEqual(main(["save"], prefs_path=self.prefs), 0)
        self.assertEqual(json.loads(outgoing.getvalue()), {"ok": True})
        self.assertNotIn(secret, outgoing.getvalue())
        self.assertEqual(plistlib.loads(self.prefs.read_bytes())["MT_AZURE_KEY"], secret)

    def test_helper_bad_payload_is_safe_and_does_not_write(self):
        for value in (b"broken", b"{}", b"[]", b"x" * (1024 * 1024 + 1)):
            output = io.StringIO()
            with patch("sys.stdin", SimpleNamespace(buffer=io.BytesIO(value))), patch("sys.stdout", output):
                main(["save"], prefs_path=self.prefs)
            self.assertFalse(json.loads(output.getvalue())["ok"])
            self.assertFalse(self.prefs.exists())

    def test_cancelled_window_does_not_write_and_payload_is_not_in_argv(self):
        state = read_window_state(self.prefs)
        secret = secrets.token_hex(10)
        state["values"]["MT_AZURE_KEY"] = secret
        completed = subprocess.CompletedProcess([], 0, stdout=b'{"action":"cancelled"}', stderr=b"")
        with patch("settings_window.read_window_state", return_value=state), \
                patch("settings_window.subprocess.run", return_value=completed) as process:
            self.assertEqual(open_window(), "cancelled")
        self.assertNotIn(secret, str(process.call_args.args[0]))
        self.assertIn(secret.encode(), process.call_args.kwargs["input"])
        self.assertFalse(self.prefs.exists())

    def test_helper_validates_without_writing(self):
        output = io.StringIO()
        with patch("sys.stdin", SimpleNamespace(buffer=io.BytesIO(json.dumps(self.request()).encode()))), \
                patch("sys.stdout", output):
            main(["validate"], prefs_path=self.prefs)
        self.assertTrue(json.loads(output.getvalue())["ok"])
        self.assertFalse(self.prefs.exists())

    def test_busy_save_or_import_preserves_configuration(self):
        request = self.request()
        with (self.path / "data" / LOCK).open("wb") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(ConfigError, "尚未完成"):
                save_window_values(request, self.prefs)
        self.assertFalse(self.prefs.exists())
        self.reload.assert_not_called()


@unittest.skipUnless(sys.platform == "darwin", "原生窗口需要 macOS")
class NativeWindowTests(LocalFiles):
    def payload(self):
        state = read_window_state(self.path / "prefs.plist")
        state["values"] = {field["variable"]: field["config"]["default"] for field in state["fields"]}
        state.update(python=sys.executable, helper=str(ROOT / "settings_window.py"), note="")
        return state

    def run_window(self, state, *arguments):
        process = subprocess.run([str(ROOT / "config_window"), *arguments], input=json.dumps(state).encode(),
                                 capture_output=True, timeout=20,
                                 env={**os.environ, "alfred_workflow_data": str(self.path / "data")})
        self.assertEqual(process.returncode, 0, "原生窗口未正常退出")
        self.assertEqual(process.stderr, b"")
        return json.loads(process.stdout)

    def test_actual_view_has_full_width_rules_masking_and_valid_pipeline(self):
        state = self.payload()
        secret = secrets.token_hex(16)
        state["values"]["MT_AZURE_KEY"] = secret
        state["previewPath"] = str(self.path / "preview.png")
        result = self.run_window(state, "--inspect", "--probe-helper")
        self.assertTrue(result["helperOK"])
        self.assertEqual(set(result["fields"]), set(state["values"]))
        self.assertEqual(result["groups"], [group["title"] for group in state["groups"]])
        self.assertEqual(len(result["separators"]), 7)
        for separator in result["separators"]:
            self.assertTrue(separator["isSeparator"])
            self.assertEqual(separator["x"], 24)
            self.assertEqual(separator["width"], result["documentWidth"] - 48)
        self.assertEqual(len(result["masked"]), 11)
        self.assertIn("MT_AZURE_KEY", result["masked"])
        self.assertNotIn(secret, json.dumps(result))
        self.assertTrue((self.path / "preview.png").read_bytes().startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertFalse((self.path / "prefs.plist").exists())

    def test_actual_window_event_loop_cancels_without_saving(self):
        original = (self.path / "data" / "config.json").read_bytes()
        self.assertEqual(self.run_window(self.payload(), "--test-cancel"), {"action": "cancelled"})
        self.assertFalse((self.path / "prefs.plist").exists())
        self.assertEqual((self.path / "data" / "config.json").read_bytes(), original)

    def test_native_save_action_reaches_real_backend_and_preserves_existing_data(self):
        # 使用真实保存后端，只替代 Alfred reload，所有配置均在测试目录。
        helper = self.path / "window_worker.py"
        helper.write_text(
            "import sys\nfrom pathlib import Path\n"
            "sys.path.insert(0, str(Path(__file__).resolve().parent.parent))\n"
            "import settings_window\nsettings_window._reload_workflow = lambda: None\n"
            "raise SystemExit(settings_window.main(prefs_path=Path(__file__).with_name('prefs.plist')))\n",
            encoding="utf-8")
        prefs = self.path / "prefs.plist"
        prefs.write_bytes(plistlib.dumps({"unrelated": {"data": b"keep"}}))
        state = self.payload()
        state["helper"] = str(helper)
        secret = secrets.token_hex(16)
        state["values"].update(MT_USE_PANEL=True, MT_DEFAULT_ENGINE="youdao", MT_YOUDAO_APP_SECRET=secret)
        original_json = (self.path / "data" / "config.json").read_bytes()
        self.assertEqual(self.run_window(state, "--test-save"), {"action": "saved"})
        actual = plistlib.loads(prefs.read_bytes())
        self.assertEqual(actual["unrelated"], {"data": b"keep"})
        self.assertEqual(actual["MT_YOUDAO_APP_SECRET"], secret)
        self.assertEqual(actual["MT_DEFAULT_ENGINE"], "youdao")
        self.assertIs(actual["MT_USE_PANEL"], True)
        self.assertEqual(prefs.stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.path / "data" / "config.json").read_bytes(), original_json)
