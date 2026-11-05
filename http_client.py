"""标准库 HTTP 传输：验证 TLS、禁重定向、有界响应，无隐式重试。"""
from __future__ import annotations

import json
from dataclasses import dataclass
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class TransportError(Exception):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@dataclass(frozen=True)
class HttpResponse:
    status: int
    payload: object
    headers: dict[str, str]


def send(request: Request, timeout: float) -> HttpResponse:
    try:
        try:
            response = build_opener(NoRedirect()).open(request, timeout=timeout)
        except HTTPError as exc:
            # 4xx/5xx 的 JSON 包含真正的错误码，不能直接当成网络错误。
            response = exc
        with response:
            content = response.read(MAX_RESPONSE_BYTES + 1)
            if len(content) > MAX_RESPONSE_BYTES:
                raise TransportError("response_too_large")
            try:
                payload = json.loads(content.decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError):
                payload = None
            return HttpResponse(response.code, payload,
                                {key.lower(): value for key, value in response.headers.items()})
    except (URLError, TimeoutError, OSError, HTTPException) as exc:
        raise TransportError(type(exc).__name__) from exc
