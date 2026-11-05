"""仅自动发现随 Workflow 安装的本地适配器，不下载或执行远程插件。"""
from __future__ import annotations

import importlib
import pkgutil

from adapters.base import BaseAdapter


def discover() -> tuple[dict[str, type[BaseAdapter]], list[str]]:
    adapters = {}
    errors = []
    prefixes = {"all", "set"}
    for entry in sorted(pkgutil.iter_modules(__path__), key=lambda item: item.name):
        if entry.name.startswith("_") or entry.name == "base":
            continue
        try:
            cls = getattr(importlib.import_module(f"{__name__}.{entry.name}"), "Adapter")
            if not issubclass(cls, BaseAdapter) or not cls.engine or not cls.prefix:
                raise ValueError("invalid_adapter")
            if cls.engine in adapters or cls.prefix in prefixes:
                raise ValueError("duplicate_adapter")
            adapters[cls.engine] = cls
            prefixes.add(cls.prefix)
        except Exception:
            # 独立列出加载失败的模块名，不把堆栈、路径或配置暴露出去。
            errors.append(entry.name)
    return adapters, errors
