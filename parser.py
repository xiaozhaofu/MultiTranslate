"""只解析开头的控制前缀，正文不做拆词或去空白处理。"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from models import LANGUAGES, TranslationRequest


class QueryError(ValueError):
    pass


@dataclass(frozen=True)
class Query:
    request: TranslationRequest
    engine: str | None = None
    all_engines: bool = False


def _automatic_target(text: str, default_target: str) -> str:
    """用字符规则选择中英互译方向，不将此判断冒充源语言检测结果。"""
    has_han = False
    has_latin = False
    for character in text:
        if not character.isalpha():
            continue
        name = unicodedata.name(character, "")
        if name.startswith(("CJK UNIFIED IDEOGRAPH-", "CJK COMPATIBILITY IDEOGRAPH-")):
            has_han = True
        elif name.startswith(("LATIN ", "FULLWIDTH LATIN ")):
            has_latin = True
        else:
            # 假名、韩文、俄文等继续使用默认目标，避免按英文强行切换。
            return default_target
    if has_han:
        return "en"
    return "zh" if has_latin else default_target


def parse_query(text: str, aliases: dict[str, str], default_target: str = "zh") -> Query:
    engine = None
    all_engines = False
    target = None
    if text.startswith("-- "):
        text = text[3:]
        return Query(TranslationRequest(text, _automatic_target(text, default_target)))
    match = re.match(r"([A-Za-z][A-Za-z0-9_-]*):", text)
    if match and (match[1] in aliases or match[1] == "all"):
        prefix = match[1]
        engine = aliases.get(prefix)
        all_engines = prefix == "all"
        text = text[match.end():]
    if text.startswith("-- "):
        text = text[3:]
    elif text.startswith("set:"):
        match = re.match(r"set:([^\s]+)(?:[ \t\r\n]|$)", text)
        if not match or match[1] not in LANGUAGES:
            raise QueryError("目标语言支持：" + " / ".join(LANGUAGES))
        target = match[1]
        text = text[match.end():]
    if target is None:
        target = _automatic_target(text, default_target)
    return Query(TranslationRequest(text, target), engine, all_engines)
