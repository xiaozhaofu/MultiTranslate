"""标准库 HTTP 传输：验证 TLS、禁重定向、有界响应，无隐式重试。"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class TransportError(Exception):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class ExplicitProxyHandler(ProxyHandler):
    def proxy_open(self, request: Request, proxy: str, protocol: str):
        # 显式代理不调用 proxy_bypass，避免 NO_PROXY/系统绕过规则改变用户选择。
        parsed = urlsplit(proxy)
        if parsed.scheme != "http" or not parsed.hostname:
            raise ValueError("invalid_proxy")
        if parsed.username is not None:
            credentials = unquote(parsed.username) + ":" + unquote(parsed.password or "")
            token = base64.b64encode(credentials.encode("utf-8")).decode("ascii")
            request.add_unredirected_header("Proxy-Authorization", "Basic " + token)
        request.set_proxy(parsed.netloc.rsplit("@", 1)[-1], "http")
        # HTTPS 保持原类型，由标准库建立 CONNECT 隧道并验证目标证书。
        return None


@dataclass(frozen=True)
class HttpResponse:
    status: int
    payload: object
    headers: dict[str, str]


def send(request: Request, timeout: float, *, proxy_mode: str = "system", proxy_url: str = "") -> HttpResponse:
    try:
        handlers = [NoRedirect()]
        if proxy_mode == "direct":
            handlers.append(ProxyHandler({}))
        elif proxy_mode == "custom":
            handlers.append(ExplicitProxyHandler({"http": proxy_url, "https": proxy_url}))
        elif proxy_mode != "system":
            raise TransportError("invalid_proxy_mode")
        try:
            response = build_opener(*handlers).open(request, timeout=timeout)
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
    except (URLError, TimeoutError, OSError, HTTPException, ValueError) as exc:
        raise TransportError(type(exc).__name__) from exc
