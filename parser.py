"""只解析开头的控制前缀，正文不做拆词或去空白处理。"""
from __future__ import annotations

import re
from dataclasses import dataclass

from models import LANGUAGES, TranslationRequest


class QueryError(ValueError):
    pass


@dataclass(frozen=True)
class Query:
    request: TranslationRequest
    engine: str | None = None
    all_engines: bool = False


def parse_query(text: str, aliases: dict[str, str], default_target: str = "zh") -> Query:
    engine = None
    all_engines = False
    target = default_target
    if text.startswith("-- "):
        return Query(TranslationRequest(text[3:], target))
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
    return Query(TranslationRequest(text, target), engine, all_engines)
