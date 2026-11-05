"""将已有 JSON 一次性补入 Alfred 面板；保留已保存的值和原 JSON。"""
from __future__ import annotations

import fcntl
import os
import plistlib
import subprocess
import tempfile
from pathlib import Path
from xml.parsers.expat import ExpatError

from config import BUNDLE_ID, ROOT, ConfigError, load_json_config

MARKER = "panel-import-v1.json"
MARKER_CONTENT = b'{"version":1}\n'
LOCK = "panel-import.lock"
ENGINES = {
    "volc": ("ak", "sk", "region"),
    "baidu": ("appid", "appkey"),
    "aliyun": ("ak", "sk", "region"),
    "azure": ("key", "region"),
    "youdao": ("app_key", "app_secret"),
}


def _panel_values(config: dict) -> dict:
    if (config["default_engine"] not in ENGINES
            or any(name not in ENGINES for name in config["fallback_order"])
            or any(name not in ENGINES for name in config["engines"])):
        raise ConfigError("JSON 含面板不支持的服务商，请继续使用 JSON 配置")
    values = {
        "MT_DEFAULT_ENGINE": config["default_engine"],
        "MT_FALLBACK_ENABLED": config["fallback_enable"],
        "MT_FALLBACK_ORDER": ",".join(config["fallback_order"]),
        "MT_PROXY_MODE": config["proxy"]["mode"],
        "MT_PROXY_URL": config["proxy"]["url"],
    }
    for engine, fields in ENGINES.items():
        settings = config["engines"].get(engine, {})
        prefix = f"MT_{engine.upper()}_"
        values[prefix + "ENABLED"] = settings.get("enable", False)
        for field in (*fields, "proxy_mode", "proxy_url"):
            if field in settings:
                values[prefix + field.upper()] = settings[field]
    return values


def _read_preferences(path: Path) -> tuple[bytes | None, dict]:
    if path.is_symlink():
        raise ConfigError("面板配置是符号链接，已跳过导入；原 JSON 保留")
    try:
        with path.open("rb") as file:
            content = file.read(1024 * 1024 + 1)
    except FileNotFoundError:
        return None, {}
    if len(content) > 1024 * 1024:
        raise ConfigError("面板配置过大，已跳过导入；原 JSON 保留")
    try:
        values = plistlib.loads(content)
    except (ValueError, ExpatError) as exc:
        raise ConfigError("面板配置无法读取，已跳过导入；原 JSON 保留") from exc
    if not isinstance(values, dict):
        raise ConfigError("面板配置格式无效，已跳过导入；原 JSON 保留")
    return content, values


def _atomic_write(path: Path, content: bytes, mode: int = 0o600) -> None:
    fd, name = tempfile.mkstemp(prefix=".panel-import-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as file:
            os.fchmod(file.fileno(), mode)
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _reload_workflow() -> None:
    # 命令只有固定 Bundle ID，密钥不进入 argv、stdout 或日志。
    script = f'tell application id "com.runningwithcrayons.Alfred" to reload workflow "{BUNDLE_ID}"'
    subprocess.run(["osascript", "-e", script], check=True, capture_output=True, timeout=10)


def _import_locked(data_dir: Path, workflow_dir: Path) -> bool:
    marker = data_dir / MARKER
    if marker.exists():
        if marker.is_symlink():
            raise ConfigError("导入记录无法识别，已保留现有面板和 JSON 配置")
        with marker.open("rb") as file:
            marker_content = file.read(len(MARKER_CONTENT) + 1)
        if marker_content != MARKER_CONTENT:
            raise ConfigError("导入记录无法识别，已保留现有面板和 JSON 配置")
        return False
    source = data_dir / "config.json"
    if not source.exists():
        return False
    values = _panel_values(load_json_config(source))
    path = workflow_dir / "prefs.plist"
    original, saved = _read_preferences(path)
    merged = {**values, **saved}  # 键存在即保留，包括主动清空和关闭的值。
    if saved == merged:
        _atomic_write(marker, MARKER_CONTENT)
        return False
    mode = path.stat().st_mode & 0o777 if original is not None else 0o600
    content = plistlib.dumps(merged, sort_keys=False)
    if _read_preferences(path)[0] != original:
        raise ConfigError("面板正在被修改，已跳过导入；请关闭面板后再运行 trconfig")
    _atomic_write(path, content)
    try:
        _reload_workflow()
        if _read_preferences(path)[1] != merged:
            raise ConfigError("面板正在被修改，已停止导入；原 JSON 保留")
        _atomic_write(marker, MARKER_CONTENT)
    except (OSError, ValueError, ExpatError, subprocess.SubprocessError) as exc:
        # 只恢复本次写入的状态，不能覆盖随后发生的用户修改。
        try:
            current = _read_preferences(path)[1]
        except (OSError, ValueError, ExpatError) as rollback_error:
            raise ConfigError("自动导入失败，面板恢复未完成；原 JSON 保留，请检查配置") from rollback_error
        if current != merged:
            raise ConfigError("面板同时被修改，未覆盖新设置；原 JSON 保留，请检查配置") from exc
        try:
            if original is None:
                path.unlink()
            else:
                _atomic_write(path, original, mode)
            _reload_workflow()
        except (OSError, ValueError, subprocess.SubprocessError) as rollback_error:
            raise ConfigError("自动导入失败，面板恢复未完成；原 JSON 保留，请检查配置") from rollback_error
        raise ConfigError("自动导入未完成，面板已恢复；原 JSON 保留，可稍后重试 trconfig") from exc
    return True


def import_json_to_panel(data_dir: Path, workflow_dir: Path = ROOT) -> bool:
    """True 表示本次已填入面板；False 表示已导入、无源文件或无需填入。"""
    if not (data_dir / "config.json").exists() and not (data_dir / MARKER).exists():
        return False
    try:
        # 非阻塞锁避免两次 trconfig 并发写入；没有配置时不创建任何文件。
        flags = os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW
        with os.fdopen(os.open(data_dir / LOCK, flags, 0o600), "wb") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ConfigError("另一次配置导入尚未完成，请稍后重试 trconfig") from exc
            return _import_locked(data_dir, workflow_dir)
    except ConfigError:
        raise
    except (OSError, ValueError, ExpatError) as exc:
        raise ConfigError("无法自动导入，请检查配置格式和权限；原 JSON 保留") from exc
