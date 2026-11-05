"""与 Alfred、厂商 HTTP 格式无关的翻译契约。"""
from __future__ import annotations

from dataclasses import asdict, dataclass

LANGUAGES = {"zh": "简体中文", "en": "英语", "ja": "日语", "ko": "韩语",
             "fr": "法语", "de": "德语", "ru": "俄语"}
ERROR_MESSAGES = {
    "quota": "额度或余额不足，请查看服务商控制台",
    "rate_limit": "访问频率超限，请稍后重试",
    "auth": "认证或权限校验失败，请检查密钥、地域和服务开通状态",
    "network": "网络请求失败或超时",
    "config": "配置不完整，请运行 trconfig 检查配置",
    "disabled": "该引擎已关闭，请运行 trconfig 启用",
    "unsupported_language": "该引擎不支持所选语种",
    "input_too_long": "文本超过单次请求限制，未截断也未发送",
    "invalid_input": "输入格式不正确",
    "service_unavailable": "翻译服务暂时不可用",
    "unknown": "接口返回异常，请稍后重试或查看诊断日志",
}


@dataclass(frozen=True)
class TranslationRequest:
    text: str
    to_lang: str = "zh"
    from_lang: str = "auto"


@dataclass
class TranslationResult:
    success: bool
    engine: str
    original_text: str
    translated_text: str = ""
    from_lang: str = "und"
    to_lang: str = "zh"
    raw_error: str = ""
    error_code: str = "ok"
    vendor_code: str = ""
    request_id: str = ""
    http_status: int = 0
    cached: bool = False
    fallback_from: str = ""
    elapsed_ms: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


def failure(engine: str, request: TranslationRequest, code: str,
            detail: str = "", **diagnostics: object) -> TranslationResult:
    return TranslationResult(False, engine, request.text, to_lang=request.to_lang,
                             from_lang=request.from_lang if request.from_lang != "auto" else "und",
                             error_code=code, raw_error=detail or ERROR_MESSAGES[code],
                             **diagnostics)
