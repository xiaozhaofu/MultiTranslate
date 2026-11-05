"""火山 TranslateText 2020-06-01；签名按官方 SignerV4 规范实现。"""
from __future__ import annotations

from datetime import datetime, timezone
from urllib.request import Request

from adapters.base import BaseAdapter
from http_client import HttpResponse
from models import TranslationRequest, TranslationResult, failure
from utils import diagnostic_token, hmac256, json_bytes, sha256


class Adapter(BaseAdapter):
    engine, name, prefix = "volc", "火山引擎", "v"
    required_fields = ("ak", "sk", "region")
    language_map = {code: code for code in ("zh", "en", "ja", "ko", "fr", "de", "ru")}

    def validate(self, request: TranslationRequest) -> TranslationResult | None:
        error = super().validate(request)
        if error:
            return error
        if self.settings["region"] != "cn-north-1":
            return failure(self.engine, request, "config", "此 TranslateText 接口签名地域须为 cn-north-1")
        return None

    def build_request(self, request: TranslationRequest) -> Request:
        payload = {"TargetLanguage": self.language_map[request.to_lang], "TextList": [request.text]}
        if request.from_lang != "auto":
            payload["SourceLanguage"] = self.language_map[request.from_lang]
        body = json_bytes(payload)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        host = "translate.volcengineapi.com"
        query = "Action=TranslateText&Version=2020-06-01"
        headers = {"content-type": "application/json", "host": host,
                   "x-content-sha256": sha256(body), "x-date": stamp}
        signed = ";".join(sorted(headers))
        canonical_headers = "".join(f"{key}:{headers[key]}\n" for key in sorted(headers))
        canonical = "\n".join(("POST", "/", query, canonical_headers, signed, sha256(body)))
        scope = f"{stamp[:8]}/{self.settings['region']}/translate/request"
        to_sign = "\n".join(("HMAC-SHA256", stamp, scope, sha256(canonical.encode("utf-8"))))
        key = self.settings["sk"].encode("utf-8")
        for part in (stamp[:8], self.settings["region"], "translate", "request"):
            key = hmac256(key, part)
        signature = hmac256(key, to_sign).hex()
        headers["authorization"] = (f"HMAC-SHA256 Credential={self.settings['ak']}/{scope}, "
                                    f"SignedHeaders={signed}, Signature={signature}")
        return Request(f"https://{host}/?{query}", data=body, headers=headers, method="POST")

    def parse_response(self, request: TranslationRequest, response: HttpResponse) -> TranslationResult:
        payload = response.payload if isinstance(response.payload, dict) else {}
        metadata = payload.get("ResponseMetadata") or {}
        request_id = diagnostic_token(metadata.get("RequestId"))
        error = metadata.get("Error")
        if response.status != 200 or error:
            error = error if isinstance(error, dict) else {}
            code = str(error.get("Code", response.status))
            category = {
                "-400": "invalid_input", "-415": "unsupported_language", "-429": "rate_limit",
                "-500": "service_unavailable", "100003": "auth", "100004": "auth",
                "100005": "auth", "100006": "auth", "100009": "auth", "100010": "auth",
                "100012": "auth", "100013": "auth", "100018": "rate_limit",
                "MissingAuthenticationToken": "auth", "InvalidTimestamp": "auth",
                "InvalidClientTokenId": "auth", "SignatureDoesNotMatch": "auth",
                "LackPolicy": "auth", "AccessDenied": "auth", "FlowLimitExceeded": "rate_limit",
                "InternalError": "service_unavailable", "InternalServiceTimeout": "network",
                "ServiceUnavailableTemp": "service_unavailable",
            }.get(code)
            # 官方机器翻译文档未穷举余额码，仅在错误明确表达余额/额度耗尽时归类。
            message = str(error.get("Message", "")).lower()
            if category is None and any(word in message for word in (
                    "insufficient balance", "quota exhausted", "quota exceeded", "欠费", "余额不足", "额度耗尽")):
                category = "quota"
            return self.error(request, response, code, category, request_id)
        rows = payload["TranslationList"]
        if not isinstance(rows, list) or len(rows) != 1:
            raise ValueError("invalid_translation_list")
        return self.success(request, rows[0]["Translation"], rows[0].get("DetectedSourceLanguage", ""),
                            request_id=request_id, http_status=response.status)
