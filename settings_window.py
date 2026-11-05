"""独立 macOS 设置窗口的配置读取、校验和安全保存；不处理翻译请求。"""
from __future__ import annotations

import hashlib
import fcntl
import json
import os
import plistlib
import subprocess
import sys
from pathlib import Path

from config import BUNDLE_ID, ROOT, ConfigError, ensure_config, validate_panel_values, workflow_paths
from panel_import import LOCK, _atomic_write, _read_preferences, _reload_workflow

MAX_INPUT_BYTES = 1024 * 1024


def _fields() -> list[dict]:
    metadata = plistlib.loads((ROOT / "info.plist").read_bytes())
    return metadata["userconfigurationconfig"]


def _revision(content: bytes | None) -> str | None:
    return hashlib.sha256(content).hexdigest() if content is not None else None


def _validate_values(values: object) -> dict:
    fields = _fields()
    if not isinstance(values, dict) or set(values) != {field["variable"] for field in fields}:
        raise ConfigError("设置字段不完整，请重新打开配置窗口")
    normalized = {}
    for field in fields:
        name, value = field["variable"], values[field["variable"]]
        if field["type"] == "checkbox":
            valid = type(value) is bool
        elif field["type"] == "popupbutton":
            valid = isinstance(value, str) and value in [pair[1] for pair in field["config"]["pairs"]]
        else:
            valid = isinstance(value, str)
            if valid and field["config"].get("trim"):
                value = value.strip()
        if not valid:
            raise ConfigError("设置格式有误，请检查“" + field["label"] + "”")
        normalized[name] = value
    validate_panel_values(normalized)
    return normalized


def read_window_state(prefs_path: Path | None = None) -> dict:
    """从磁盘最新值读取；未保存的字段才使用 Alfred 环境及发行默认值。"""
    content, saved = _read_preferences(prefs_path or ROOT / "prefs.plist")
    fields = _fields()
    values = {}
    for field in fields:
        name = field["variable"]
        value = saved.get(name, os.environ.get(name, field["config"]["default"]))
        if field["type"] == "checkbox" and value in ("0", "1"):
            value = value == "1"
        values[name] = value
    # 不校验用户尚未填好的代理地址，允许打开窗口修正；保存时完整校验。
    for field in fields:
        value = values[field["variable"]]
        if field["type"] == "checkbox" and type(value) is not bool:
            raise ConfigError("已保存的开关格式无效，请通过原生面板修正")
        if field["type"] != "checkbox" and not isinstance(value, str):
            raise ConfigError("已保存的字段格式无效，请通过原生面板修正")
    groups = [
        {"title": "全局设置", "variables": ["MT_USE_PANEL", "MT_DEFAULT_ENGINE", "MT_FALLBACK_ENABLED", "MT_FALLBACK_ORDER"]},
        {"title": "全局代理", "variables": ["MT_PROXY_MODE", "MT_PROXY_URL"]},
    ]
    labels = {field["variable"]: field["label"] for field in fields}
    for engine in ("VOLC", "BAIDU", "ALIYUN", "AZURE", "YOUDAO"):
        prefix = "MT_" + engine + "_"
        enable = prefix + "ENABLED"
        groups.append({"title": labels[enable], "enable": enable,
                       "variables": [field["variable"] for field in fields if field["variable"].startswith(prefix)]})
    groups.append({"title": "运行环境", "variables": ["PYTHON_BINARY"]})
    return {"fields": fields, "values": values, "groups": groups, "snapshot": _revision(content)}


def save_window_values(request: dict, prefs_path: Path | None = None) -> None:
    """与 JSON 导入共享非阻塞锁，避免两个窗口同时写入。"""
    data_dir = workflow_paths()[0]
    data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    with os.fdopen(os.open(data_dir / LOCK, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600), "wb") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ConfigError("另一次配置保存或导入尚未完成，请稍后重试") from exc
        _save_window_values(request, prefs_path)


def _save_window_values(request: dict, prefs_path: Path | None) -> None:
    """保留未知键，校验打开时的指纹；失败恢复原始字节和存在性。"""
    if not isinstance(request, dict) or "snapshot" not in request:
        raise ConfigError("窗口状态不完整，请重新打开设置")
    values = _validate_values(request.get("values"))
    path = prefs_path or ROOT / "prefs.plist"
    original, saved = _read_preferences(path)
    if _revision(original) != request["snapshot"]:
        raise ConfigError("配置已在其他窗口改变，请重新打开后再保存")
    merged = {**saved, **values}
    mode = path.stat().st_mode & 0o777 if original is not None else 0o600
    content = plistlib.dumps(merged, sort_keys=False)
    if _read_preferences(path)[0] != original:
        raise ConfigError("配置正在被修改，请重新打开后再保存")
    _atomic_write(path, content)
    try:
        _reload_workflow()
        if _read_preferences(path)[1] != merged:
            raise ConfigError("配置在保存期间发生改变，请重新打开检查")
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        try:
            current = _read_preferences(path)[1]
        except (OSError, ValueError) as read_error:
            raise ConfigError("保存失败且无法读取恢复状态，请通过原生面板检查") from read_error
        if current != merged:
            raise ConfigError("检测到其他修改，已保留新值，请重新打开检查") from exc
        try:
            if original is None:
                path.unlink()
            else:
                _atomic_write(path, original, mode)
            _reload_workflow()
        except (OSError, ValueError, subprocess.SubprocessError) as restore_error:
            raise ConfigError("保存失败且恢复未完成，请通过原生面板检查；原 JSON 未改动") from restore_error
        raise ConfigError("保存未完成，原设置已恢复；请稍后重试") from exc


def open_window(note: str = "") -> str:
    state = read_window_state()
    state.update(python=sys.executable, helper=str(ROOT / "settings_window.py"), note=note)
    try:
        process = subprocess.run([str(ROOT / "config_window")],
                                 input=json.dumps(state, ensure_ascii=False).encode("utf-8"),
                                 capture_output=True, check=True)
        result = json.loads(process.stdout)
        if result.get("action") not in ("saved", "cancelled"):
            raise ValueError("invalid_window_result")
        return result["action"]
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise ConfigError("独立配置窗口未能完成，请使用 trconfig-native 或 trconfig-json") from exc


def main(argv: list[str] | None = None, *, prefs_path: Path | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    try:
        if argv in (["save"], ["validate"]):
            content = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
            if len(content) > MAX_INPUT_BYTES:
                raise ConfigError("配置内容过大，未保存")
            request = json.loads(content.decode("utf-8"))
            if argv == ["save"]:
                save_window_values(request, prefs_path)
            else:
                _validate_values(request.get("values") if isinstance(request, dict) else None)
        elif argv == ["open-json"]:
            path = ensure_config(workflow_paths()[0])
            subprocess.run(["open", "-t", str(path)], check=True, capture_output=True, timeout=10)
        elif argv == ["open-native"]:
            script = f'tell application id "com.runningwithcrayons.Alfred" to reveal workflow "{BUNDLE_ID}" configuration true'
            subprocess.run(["osascript", "-e", script], check=True, capture_output=True, timeout=10)
        else:
            raise ConfigError("无效的配置操作")
        result = {"ok": True}
    except ConfigError as exc:
        result = {"ok": False, "message": str(exc)}
    except (ValueError, UnicodeError):
        result = {"ok": False, "message": "配置数据格式无效，未保存"}
    except (OSError, subprocess.SubprocessError):
        result = {"ok": False, "message": "配置操作未完成，请检查权限或使用备用入口"}
    except Exception:
        result = {"ok": False, "message": "配置操作未完成，请通过原生面板检查"}
    sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
