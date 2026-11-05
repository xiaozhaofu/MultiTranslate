"""有道智云文本翻译：SHA256 v3 签名，严格使用上层选定的目标语言。"""
from __future__ import annotations

import hashlib
import time
from urllib.request import Request
from uuid import uuid4

from adapters.base import BaseAdapter
from http_client import HttpResponse
from models import TranslationRequest, TranslationResult
from utils import form_bytes


class Adapter(BaseAdapter):
    engine, name, prefix = "youdao", "有道翻译", "y"
    required_fields = ("app_key", "app_secret")
    language_map = {"zh": "zh-CHS", "en": "en", "ja": "ja", "ko": "ko",
                    "fr": "fr", "de": "de", "ru": "ru"}
    max_length = 5000

    def build_request(self, request: TranslationRequest) -> Request:
        salt = str(uuid4())
        curtime = str(int(time.time()))
        text = request.text
        # 只缩写签名输入；发送的 q 始终保留完整原文。
        sign_input = text if len(text) <= 20 else text[:10] + str(len(text)) + text[-10:]
        plain = self.settings["app_key"] + sign_input + salt + curtime + self.settings["app_secret"]
        payload = {
            "q": text,
            "from": self.language_map.get(request.from_lang, "auto"),
            "to": self.language_map[request.to_lang],
            "appKey": self.settings["app_key"],
            "salt": salt,
            "curtime": curtime,
            "signType": "v3",
            "sign": hashlib.sha256(plain.encode("utf-8")).hexdigest(),
            # 默认 false 会自行中英换向，必须关闭以遵守手动 set:。
            "strict": "true",
        }
        return Request("https://openapi.youdao.com/api", data=form_bytes(payload),
                       headers={"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"},
                       method="POST")

    def parse_response(self, request: TranslationRequest, response: HttpResponse) -> TranslationResult:
        payload = response.payload if isinstance(response.payload, dict) else {}
        code = str(payload.get("errorCode", ""))
        if response.status != 200 or code != "0":
            category = {
                "101": "invalid_input", "102": "unsupported_language", "103": "input_too_long",
                "104": "invalid_input", "105": "auth", "106": "invalid_input", "107": "invalid_input",
                "108": "auth", "110": "auth", "111": "auth", "112": "invalid_input",
                "113": "invalid_input", "116": "invalid_input", "201": "auth", "202": "auth",
                "203": "auth", "205": "auth", "206": "auth", "207": "auth",
                "301": "service_unavailable", "302": "service_unavailable",
                "303": "service_unavailable", "304": "service_unavailable",
                "308": "invalid_input", "309": "invalid_input", "310": "auth",
                "401": "quota", "411": "rate_limit", "412": "rate_limit",
            }.get(code)
            return self.error(request, response, code or "invalid_response", category)
        rows = payload.get("translation")
        if not isinstance(rows, list) or not rows or any(not isinstance(row, str) for row in rows):
            raise ValueError("invalid_translation_list")
        direction = payload.get("l", "")
        if not isinstance(direction, str):
            raise ValueError("invalid_language_pair")
        # 文档示例使用 EN，而语种表使用 en；未知源语种继续返回 und。
        detected = direction.split("2", 1)[0].lower()
        source = next((code for code in self.language_map.values() if code.lower() == detected), "")
        return self.success(request, "\n".join(rows), source, http_status=response.status)
