"""适配器契约及共同校验；不包含 Alfred 交互。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable
from urllib.request import Request

from http_client import HttpResponse, TransportError, send
from models import TranslationRequest, TranslationResult, failure
from utils import diagnostic_token


class BaseAdapter(ABC):
    engine = ""
    name = ""
    prefix = ""
    required_fields: tuple[str, ...] = ()
    language_map: dict[str, str] = {}
    max_length = 5000
    length_unit = "字符"

    def __init__(self, settings: dict, transport: Callable = send):
        self.settings = settings
        self.transport = transport

    def validate(self, request: TranslationRequest) -> TranslationResult | None:
        if not request.text.strip():
            return failure(self.engine, request, "invalid_input", "请输入要翻译的文本")
        if not self.settings.get("enable", False):
            return failure(self.engine, request, "disabled")
        missing = [field for field in self.required_fields if not self.settings.get(field, "").strip()]
        if missing:
            return failure(self.engine, request, "config", "缺少 " + " / ".join(missing) + "，请运行 trconfig")
        if request.to_lang not in self.language_map or (request.from_lang != "auto" and request.from_lang not in self.language_map):
            return failure(self.engine, request, "unsupported_language")
        length = len(request.text.encode("utf-8")) if self.length_unit == "UTF-8 字节" else len(request.text)
        if length > self.max_length:
            return failure(self.engine, request, "input_too_long",
                           f"上限 {self.max_length} {self.length_unit}，当前 {length}；未截断也未发送")
        return None

    def translate(self, request: TranslationRequest, timeout: float) -> TranslationResult:
        error = self.validate(request)
        if error:
            return error
        try:
            response = self.transport(self.build_request(request), timeout)
            return self.parse_response(request, response)
        except TransportError as exc:
            return failure(self.engine, request, "network", vendor_code=diagnostic_token(str(exc)))
        except Exception as exc:
            # 异常类型送回主线程的诊断日志，不记录可能含密钥的异常正文。
            return failure(self.engine, request, "unknown", vendor_code=type(exc).__name__)

    def success(self, request: TranslationRequest, text: str, source: str = "", **info: object) -> TranslationResult:
        if not isinstance(text, str) or not text.strip():
            raise ValueError("empty_translation")
        reverse = {value: key for key, value in self.language_map.items()}
        source = request.from_lang if request.from_lang != "auto" else reverse.get(source, "und")
        return TranslationResult(True, self.engine, request.text, text, source, request.to_lang, **info)

    def error(self, request: TranslationRequest, response: HttpResponse, code: object,
              category: str | None = None, request_id: object = "") -> TranslationResult:
        if category is None:
            category = {401: "auth", 403: "auth", 408: "network", 429: "rate_limit",
                        500: "service_unavailable", 502: "service_unavailable",
                        503: "service_unavailable", 504: "network"}.get(response.status, "unknown")
        return failure(self.engine, request, category, vendor_code=diagnostic_token(code),
                       request_id=diagnostic_token(request_id), http_status=response.status)

    @abstractmethod
    def build_request(self, request: TranslationRequest) -> Request:
        raise NotImplementedError

    @abstractmethod
    def parse_response(self, request: TranslationRequest, response: HttpResponse) -> TranslationResult:
        raise NotImplementedError
