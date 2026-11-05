"""百度通用文本翻译；签名使用未 URL 编码的原文。"""
from __future__ import annotations

import hashlib
import secrets
from urllib.request import Request

from adapters.base import BaseAdapter
from http_client import HttpResponse
from models import TranslationRequest, TranslationResult
from utils import form_bytes


class Adapter(BaseAdapter):
    engine, name, prefix = "baidu", "百度翻译", "b"
    required_fields = ("appid", "appkey")
    language_map = {"zh": "zh", "en": "en", "ja": "jp", "ko": "kor",
                    "fr": "fra", "de": "de", "ru": "ru"}
    max_length = 6000
    length_unit = "UTF-8 字节"

    def build_request(self, request: TranslationRequest) -> Request:
        salt = secrets.token_hex(16)
        plain = self.settings["appid"] + request.text + salt + self.settings["appkey"]
        # MD5 仅用于该服务规定的兼容性签名，不用于密码存储。
        signature = hashlib.md5(plain.encode("utf-8")).hexdigest()
        payload = {"q": request.text, "from": self.language_map.get(request.from_lang, "auto"),
                   "to": self.language_map[request.to_lang], "appid": self.settings["appid"],
                   "salt": salt, "sign": signature}
        return Request("https://fanyi-api.baidu.com/api/trans/vip/translate", data=form_bytes(payload),
                       headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")

    def parse_response(self, request: TranslationRequest, response: HttpResponse) -> TranslationResult:
        payload = response.payload if isinstance(response.payload, dict) else {}
        code = str(payload.get("error_code", ""))
        if response.status != 200 or code not in ("", "52000"):
            category = {"52001": "network", "52002": "service_unavailable", "52003": "auth",
                        "54000": "invalid_input", "54001": "auth", "54003": "rate_limit",
                        "54004": "quota", "54005": "rate_limit", "58000": "auth",
                        "58001": "unsupported_language", "58002": "auth"}.get(code)
            return self.error(request, response, code or response.status, category)
        rows = payload["trans_result"]
        if not isinstance(rows, list) or not rows:
            raise ValueError("invalid_translation_list")
        # 百度按段返回多个结果，不能只取第一段。
        translated = "\n".join(row["dst"] for row in rows)
        return self.success(request, translated, payload.get("from", ""), http_status=response.status)
