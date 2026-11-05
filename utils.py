"""小型编码工具；各家的签名算法仍保留在各自适配器中。"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
from urllib.parse import quote, urlencode


def json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def form_bytes(values: dict[str, str]) -> bytes:
    return urlencode(sorted(values.items()), quote_via=quote, safe="~").encode("utf-8")


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def hmac256(key: bytes, text: str) -> bytes:
    return hmac.new(key, text.encode("utf-8"), hashlib.sha256).digest()


def diagnostic_token(value: object) -> str:
    """不将厂商回显的文本、密钥或路径带入面板和日志。"""
    text = str(value) if value is not None else ""
    return text if re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}", text) else ""


def single_line(text: str, limit: int = 180) -> str:
    return " ".join(text.split())[:limit]
