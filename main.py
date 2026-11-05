"""Alfred 入口：配置、解析和输出；服务商逻辑由适配器承担。"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from adapters import discover
from config import ConfigError, ensure_config, load_config, workflow_paths
from output import format_results, notice
from parser import QueryError, parse_query
from scheduler import Scheduler


def configure_logging(directory: Path, enabled: bool) -> None:
    logger = logging.getLogger("multitranslate")
    for handler in logger.handlers:
        handler.close()
    logger.handlers.clear()
    logger.propagate = False
    logger.addHandler(logging.NullHandler())
    logger.setLevel(logging.INFO if enabled else logging.CRITICAL)
    if enabled:
        try:
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            path = directory / "workflow.log"
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            os.close(fd)
            handler = RotatingFileHandler(path, maxBytes=256 * 1024, backupCount=1, encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
            logger.addHandler(handler)
            logging.raiseExceptions = False
        except OSError:
            # 日志不可写不应阻断翻译或把本机路径带进错误输出。
            print("日志目录不可写，本次已关闭文件日志", file=sys.stderr)


def run(argv: list[str]) -> dict:
    data_dir, cache_dir = workflow_paths()
    if argv == ["configure"]:
        try:
            path = ensure_config(data_dir)
            subprocess.run(["open", "-t", str(path)], check=True, capture_output=True)
            return {"items": [notice("已打开配置文件", "填写密钥并保存，然后使用 tr 翻译")]}
        except (OSError, subprocess.SubprocessError):
            return {"items": [notice("无法打开配置文件", "请检查默认文本编辑器和数据目录权限")]}
    if not argv or argv[0] != "translate" or len(argv) > 2:
        return {"items": [notice("用法：tr 要翻译的文本", "配置密钥：trconfig")]}
    config = load_config(data_dir)
    configure_logging(cache_dir, config["log_enabled"])
    classes, errors = discover()
    aliases = {cls.prefix: engine for engine, cls in classes.items()}
    query = parse_query(argv[1] if len(argv) == 2 else "", aliases, config["target_lang_default"])
    if not query.request.text.strip():
        result = {"items": [notice("请输入要翻译的文本", "tr hello · tr v:set:en 你好 · tr all:hello · 配置：trconfig")]}
    else:
        adapters = {engine: cls(config["engines"].get(engine, {})) for engine, cls in classes.items()}
        results = Scheduler(config, adapters, cache_dir).translate(query)
        result = format_results(results, {engine: cls.name for engine, cls in classes.items()})
    for name in errors:
        result["items"].append(notice("适配器加载失败：" + name, "请检查安装文件；其他引擎仍可使用"))
    return result


def main() -> int:
    try:
        result = run(sys.argv[1:])
    except (ConfigError, QueryError) as exc:
        result = {"items": [notice("请检查输入或配置", str(exc))]}
    except Exception as exc:
        # 不打印异常正文/堆栈：它们可能携带用户路径、文本和认证信息。
        print("翻译入口异常：" + type(exc).__name__, file=sys.stderr)
        result = {"items": [notice("翻译暂时无法完成", "请检查配置和安装文件，或查看 Alfred 调试器")]}
    sys.stdout.write(json.dumps(result, ensure_ascii=False, allow_nan=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
