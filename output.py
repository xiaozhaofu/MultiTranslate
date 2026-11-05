"""唯一的 Alfred 输出层；展示可精简，复制始终保留完整译文。"""
from __future__ import annotations

from models import TranslationResult
from utils import single_line


def notice(title: str, subtitle: str = "") -> dict:
    return {"title": title, "subtitle": subtitle, "valid": False}


def format_results(results: list[TranslationResult], names: dict[str, str]) -> dict:
    items = []
    for result in results:
        name = names.get(result.engine, result.engine)
        if not result.success:
            extra = f" [{result.vendor_code}]" if result.vendor_code else ""
            items.append(notice(f"[{name}] 翻译未完成", result.raw_error + extra))
            continue
        flags = " · 缓存" if result.cached else ""
        if result.fallback_from:
            flags += " · 已从 " + names.get(result.fallback_from, result.fallback_from) + " 兜底"
        items.append({
            "title": single_line(result.translated_text, 300),
            "subtitle": f"[{name}] {result.from_lang} → {result.to_lang}{flags} · {single_line(result.original_text)}",
            "arg": result.translated_text,
            "valid": True,
            "text": {"copy": result.translated_text, "largetype": result.translated_text},
        })
    return {"items": items}
