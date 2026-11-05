"""阿里 TranslateGeneral 2018-10-12，ACS3-HMAC-SHA256 表单签名。"""
from __future__ import annotations

from datetime import datetime, timezone
from urllib.request import Request
from uuid import uuid4

from adapters.base import BaseAdapter
from http_client import HttpResponse
from models import TranslationRequest, TranslationResult, failure
from utils import diagnostic_token, form_bytes, hmac256, sha256


class Adapter(BaseAdapter):
    engine, name, prefix = "aliyun", "阿里云", "a"
    required_fields = ("ak", "sk", "region")
    language_map = {code: code for code in ("zh", "en", "ja", "ko", "fr", "de", "ru")}

    def validate(self, request: TranslationRequest) -> TranslationResult | None:
        error = super().validate(request)
        if error:
            return error
        if self.settings["region"] != "cn-hangzhou":
            return failure(self.engine, request, "config", "当前适配器使用杭州通用翻译接入点，region 须为 cn-hangzhou")
        return None

    def build_request(self, request: TranslationRequest) -> Request:
        host = "mt.cn-hangzhou.aliyuncs.com"
        body = form_bytes({"FormatType": "text", "SourceLanguage": self.language_map.get(request.from_lang, "auto"),
                           "TargetLanguage": self.language_map[request.to_lang],
                           "SourceText": request.text, "Scene": "general"})
        headers = {"content-type": "application/x-www-form-urlencoded", "host": host,
                   "x-acs-action": "TranslateGeneral", "x-acs-version": "2018-10-12",
                   "x-acs-date": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                   "x-acs-signature-nonce": str(uuid4()), "x-acs-content-sha256": sha256(body)}
        signed = ";".join(sorted(headers))
        canonical_headers = "".join(f"{key}:{headers[key].strip()}\n" for key in sorted(headers))
        canonical = "\n".join(("POST", "/", "", canonical_headers, signed, sha256(body)))
        to_sign = "ACS3-HMAC-SHA256\n" + sha256(canonical.encode("utf-8"))
        signature = hmac256(self.settings["sk"].encode("utf-8"), to_sign).hex()
        headers["authorization"] = (f"ACS3-HMAC-SHA256 Credential={self.settings['ak']},"
                                    f"SignedHeaders={signed},Signature={signature}")
        return Request(f"https://{host}/", data=body, headers=headers, method="POST")

    def parse_response(self, request: TranslationRequest, response: HttpResponse) -> TranslationResult:
        payload = response.payload if isinstance(response.payload, dict) else {}
        code = str(payload.get("Code", ""))
        request_id = diagnostic_token(payload.get("RequestId"))
        if response.status != 200 or code != "200":
            category = {"10001": "network", "10002": "service_unavailable",
                        "10003": "invalid_input", "10004": "invalid_input",
                        "10005": "unsupported_language", "10006": "unsupported_language",
                        "10007": "service_unavailable", "10008": "input_too_long",
                        "10009": "auth", "10010": "auth", "10011": "auth",
                        "10012": "service_unavailable",
                        "InvalidAccessKeyId.NotFound": "auth", "SignatureDoesNotMatch": "auth",
                        "InvalidSecurityToken.Expired": "auth", "Forbidden.RAM": "auth"}.get(code)
            if code.startswith("Throttling"):
                category = "rate_limit"
            elif code.startswith("InvalidAccessKeyId") or code.startswith("Signature"):
                category = "auth"
            elif code == "10013":
                # 此码同时表示未开通或欠费，不向用户断言一定是额度耗尽。
                return failure(self.engine, request, "auth", "服务未开通或账户欠费，请检查阿里云控制台",
                               vendor_code=code, request_id=request_id, http_status=response.status)
            message = str(payload.get("Message", "")).lower()
            if category is None and any(word in message for word in (
                    "insufficient balance", "quota exhausted", "欠费", "余额不足", "额度耗尽")):
                category = "quota"
            return self.error(request, response, code or response.status, category, request_id)
        data = payload["Data"]
        return self.success(request, data["Translated"], data.get("DetectedLanguage", ""),
                            request_id=request_id, http_status=response.status)
