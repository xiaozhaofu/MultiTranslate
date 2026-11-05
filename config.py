"""隔离读取原生面板与 JSON 配置，保留用户 JSON 原文。"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
from urllib.parse import urlsplit

from models import LANGUAGES

BUNDLE_ID = "local.multitranslate.alfred"
VERSION = "1.6.0"
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


def _panel_checkbox(name: str, default: bool = False, *, environment: dict | None = None) -> bool:
    source = os.environ if environment is None else environment
    value = source.get(name, "1" if default else "0")
    if value not in ("0", "1"):
        raise ConfigError(f"面板字段 {name} 无效，请重新保存 Workflow 配置")
    return value == "1"


def _panel_values(defaults: dict, environment: dict | None = None) -> dict:
    source = os.environ if environment is None else environment
    engine = source.get("MT_DEFAULT_ENGINE", defaults["default_engine"]).strip()
    if engine not in defaults["engines"]:
        raise ConfigError("请在 Workflow 配置中选择有效的默认服务商")
    order = source.get("MT_FALLBACK_ORDER", ",".join(defaults["fallback_order"]))
    values = {
        "default_engine": engine,
        "fallback_enable": _panel_checkbox("MT_FALLBACK_ENABLED", True, environment=source),
        "fallback_order": [name.strip() for name in order.split(",") if name.strip()],
        "proxy": {"mode": source.get("MT_PROXY_MODE", "system").strip(),
                  "url": source.get("MT_PROXY_URL", "").strip()},
        "engines": {},
    }
    if any(name not in defaults["engines"] for name in values["fallback_order"]):
        raise ConfigError("面板兜底顺序仅支持 " + "、".join(defaults["engines"]))
    for name, fields in defaults["engines"].items():
        prefix = f"MT_{name.upper()}_"
        settings = {"enable": _panel_checkbox(prefix + "ENABLED", environment=source)}
        for field, default in fields.items():
            if field != "enable":
                settings[field] = source.get(prefix + field.upper(), default).strip()
        values["engines"][name] = settings
    return values


def _json_values(path: Path) -> dict:
    try:
        with path.open("rb") as file:
            content = file.read(1024 * 1024 + 1)
        if len(content) > 1024 * 1024:
            raise ConfigError("config.json 过大，请运行 trconfig-json 检查")
        values = json.loads(content.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ConfigError("无法读取 config.json，请运行 trconfig-json 检查格式和权限") from exc
    if not isinstance(values, dict):
        raise ConfigError("config.json 顶层必须是对象")
    return values


def load_config(data_dir: Path) -> dict:
    defaults = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
    # 显式切换来源，避免面板空密钥意外继续使用 JSON 中的另一组凭据。
    if _panel_checkbox("MT_USE_PANEL"):
        values = _panel_values(defaults)
    else:
        try:
            values = _json_values(ensure_config(data_dir))
        except OSError as exc:
            raise ConfigError("无法读取 config.json，请运行 trconfig-json 检查格式和权限") from exc
    return _validate_config(values, defaults)


def load_json_config(path: Path) -> dict:
    """导入只读取已存在的 JSON；忽略面板开关，不创建或改写用户文件。"""
    defaults = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
    return _validate_config(_json_values(path), defaults)


def validate_panel_values(values: dict) -> dict:
    """校验独立窗口提交的值，不修改进程环境或用户文件。"""
    environment = {key: ("1" if value else "0") if type(value) is bool else value
                   for key, value in values.items()}
    _panel_checkbox("MT_USE_PANEL", environment=environment)
    defaults = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
    return _validate_config(_panel_values(defaults, environment), defaults)


def _validate_config(values: dict, defaults: dict) -> dict:
    config = {**defaults, **values}
    proxy = values.get("proxy", {})
    if not isinstance(proxy, dict):
        raise ConfigError("proxy 必须是包含 mode 和 url 的对象")
    config["proxy"] = {**defaults["proxy"], **proxy}
    config["proxy"]["url"] = _validate_proxy(config["proxy"]["mode"], config["proxy"]["url"], "全局代理")
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
        merged = {"proxy_mode": "inherit", "proxy_url": "", **defaults["engines"].get(name, {}), **settings}
        if type(merged.get("enable", False)) is not bool:
            raise ConfigError("引擎 enable 必须是 true 或 false")
        if any(not isinstance(value, str) for key, value in merged.items() if key != "enable"):
            raise ConfigError("引擎密钥、地域和代理设置必须是字符串")
        merged["proxy_url"] = _validate_proxy(merged["proxy_mode"], merged["proxy_url"], "厂商代理", allow_inherit=True)
        config["engines"][name] = merged
    return config


def _validate_proxy(mode: str, url: str, label: str, *, allow_inherit: bool = False) -> str:
    allowed = ("inherit", "system", "direct", "custom") if allow_inherit else ("system", "direct", "custom")
    if not isinstance(mode, str) or mode not in allowed:
        raise ConfigError(label + "模式无效，请在 trconfig 中重新选择")
    if not isinstance(url, str):
        raise ConfigError(label + "地址必须是字符串")
    url = url.strip()
    if mode != "custom":
        return url
    try:
        parsed = urlsplit(url)
        port = parsed.port
        invalid = (parsed.scheme != "http" or not parsed.hostname or parsed.path not in ("", "/")
                   or parsed.query or parsed.fragment or "?" in url or "#" in url
                   or (port is not None and not 1 <= port <= 65535)
                   or parsed.netloc.endswith(":")
                   or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in url)
                   or any(char in parsed.hostname for char in "\\/%@")
                   or parsed.username == "")
    except ValueError:
        invalid = True
    if invalid:
        # 不拼接地址：其中可能包含代理密码。
        raise ConfigError(label + "地址无效，请填写 http://主机:端口；不支持 SOCKS、路径或查询参数")
    return url


def resolve_proxy(config: dict, settings: dict) -> tuple[str, str]:
    """厂商可独立覆盖全局；返回值仅用于该厂商的 HTTP 传输。"""
    mode = settings.get("proxy_mode", "inherit")
    if mode == "inherit":
        mode, url = config["proxy"]["mode"], config["proxy"]["url"]
    else:
        url = settings.get("proxy_url", "")
    return mode, url if mode == "custom" else ""
