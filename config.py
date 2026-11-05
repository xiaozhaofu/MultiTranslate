"""配置仅写入用户数据目录，绝不改写安装包内模板。"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path

from models import LANGUAGES

BUNDLE_ID = "local.multitranslate.alfred"
VERSION = "1.0.0"
ROOT = Path(__file__).resolve().parent


class ConfigError(ValueError):
    pass


def workflow_paths() -> tuple[Path, Path]:
    home = Path.home()
    data = Path(os.environ.get("alfred_workflow_data") or
                home / "Library" / "Application Support" / "Alfred" / "Workflow Data" / BUNDLE_ID)
    cache = Path(os.environ.get("alfred_workflow_cache") or
                 home / "Library" / "Caches" / "Alfred" / "Workflow Data" / BUNDLE_ID)
    return data, cache


def ensure_config(data_dir: Path) -> Path:
    data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = data_dir / "config.json"
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return path
    with os.fdopen(fd, "wb") as file:
        file.write((ROOT / "config.example.json").read_bytes())
    return path


def load_config(data_dir: Path) -> dict:
    try:
        path = ensure_config(data_dir)
        if path.stat().st_size > 1024 * 1024:
            raise ConfigError("config.json 过大，请运行 trconfig 检查")
        values = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ConfigError("无法读取 config.json，请运行 trconfig 检查 JSON 格式和权限") from exc
    if not isinstance(values, dict):
        raise ConfigError("config.json 顶层必须是对象")
    defaults = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
    config = {**defaults, **values}
    for key in ("fallback_enable", "cache_enabled", "log_enabled"):
        if type(config[key]) is not bool:
            raise ConfigError(f"{key} 必须是 true 或 false")
    for key, low, high in (("request_timeout_seconds", 0.1, 10),
                           ("query_timeout_seconds", 0.1, 60),
                           ("cache_ttl_seconds", 1, 3600),
                           ("cache_max_text_chars", 1, 50000),
                           ("cache_max_entries", 1, 1000)):
        value = config[key]
        if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
            raise ConfigError(f"{key} 必须在 {low} 到 {high} 之间")
        if key in ("cache_max_text_chars", "cache_max_entries") and type(value) is not int:
            raise ConfigError(f"{key} 必须是整数")
    if not isinstance(config["default_engine"], str) or not config["default_engine"]:
        raise ConfigError("default_engine 必须是非空服务商 ID")
    if not isinstance(config["target_lang_default"], str) or config["target_lang_default"] not in LANGUAGES:
        raise ConfigError("target_lang_default 不是支持的语言")
    order = config["fallback_order"]
    if not isinstance(order, list) or any(not isinstance(item, str) or not item for item in order):
        raise ConfigError("fallback_order 必须是服务商 ID 数组")
    engines = values.get("engines", defaults["engines"])
    if not isinstance(engines, dict):
        raise ConfigError("engines 必须是对象")
    # 不补回被用户从 engines 删除的服务商，新增服务商可直接添加配置。
    config["engines"] = {}
    for name, settings in engines.items():
        if not isinstance(settings, dict):
            raise ConfigError("每个引擎的配置必须是对象")
        merged = {**defaults["engines"].get(name, {}), **settings}
        if type(merged.get("enable", False)) is not bool:
            raise ConfigError("引擎 enable 必须是 true 或 false")
        if any(not isinstance(value, str) for key, value in merged.items() if key != "enable"):
            raise ConfigError("引擎密钥和地域必须是字符串")
        config["engines"][name] = merged
    return config
