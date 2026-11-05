"""Azure Translator 3.0：全球公有云端点，使用订阅密钥鉴权。"""
from __future__ import annotations

from urllib.parse import urlencode
from urllib.request import Request

from adapters.base import BaseAdapter
from http_client import HttpResponse
from models import TranslationRequest, TranslationResult
from utils import diagnostic_token, json_bytes


class Adapter(BaseAdapter):
    engine, name, prefix = "azure", "微软 Azure", "m"
    required_fields = ("key",)
    language_map = {"zh": "zh-Hans", "en": "en", "ja": "ja", "ko": "ko",
                    "fr": "fr", "de": "de", "ru": "ru"}
    max_length = 50000

    def build_request(self, request: TranslationRequest) -> Request:
        query = {"api-version": "3.0", "to": self.language_map[request.to_lang], "textType": "plain"}
        if request.from_lang != "auto":
            query["from"] = self.language_map[request.from_lang]
        headers = {"Ocp-Apim-Subscription-Key": self.settings["key"],
                   "Content-Type": "application/json; charset=UTF-8"}
        if self.settings.get("region", "").strip():
            headers["Ocp-Apim-Subscription-Region"] = self.settings["region"].strip()
        return Request("https://api.cognitive.microsofttranslator.com/translate?" + urlencode(query),
                       data=json_bytes([{"Text": request.text}]), headers=headers, method="POST")

    def parse_response(self, request: TranslationRequest, response: HttpResponse) -> TranslationResult:
        payload = response.payload
        request_id = diagnostic_token(response.headers.get("x-requestid"))
        if response.status != 200 or not isinstance(payload, list):
            error = payload.get("error", {}) if isinstance(payload, dict) else {}
            code = str(error.get("code", response.status))
            category = {"403001": "quota", "400050": "input_too_long",
                        "400035": "unsupported_language", "400036": "unsupported_language"}.get(code)
            return self.error(request, response, code, category, request_id)
        if len(payload) != 1:
            raise ValueError("invalid_translation_list")
        row = payload[0]
        return self.success(request, row["translations"][0]["text"],
                            row.get("detectedLanguage", {}).get("language", ""),
                            request_id=request_id, http_status=response.status)
