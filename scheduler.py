"""按元数据调度；超时后仅主线程决定结果，不等待阻塞网络线程。"""
from __future__ import annotations

import logging
import queue
import threading
import time
from pathlib import Path

from adapters.base import BaseAdapter
from cache import TranslationCache
from models import TranslationRequest, TranslationResult, failure
from parser import Query

FALLBACK_ERRORS = {"quota", "rate_limit", "auth", "network", "service_unavailable"}


class Scheduler:
    def __init__(self, config: dict, adapters: dict[str, BaseAdapter], cache_dir: Path):
        self.config = config
        self.adapters = adapters
        self.cache = TranslationCache(cache_dir, config)
        self.logger = logging.getLogger("multitranslate")

    def preflight(self, engine: str, request: TranslationRequest) -> TranslationResult | None:
        adapter = self.adapters.get(engine)
        if adapter is None:
            return failure(engine, request, "config", "未找到该引擎适配器，请检查安装及配置")
        return adapter.validate(request)

    def execute(self, engines: list[str], request: TranslationRequest, deadline: float) -> list[TranslationResult]:
        results = {}
        pending = {}
        inbox = queue.Queue()

        def worker(engine: str, timeout: float, started: float) -> None:
            try:
                result = self.adapters[engine].translate(request, timeout)
            except Exception as exc:
                result = failure(engine, request, "unknown", vendor_code=type(exc).__name__)
            inbox.put((engine, result, time.monotonic(), started))

        for engine in engines:
            error = self.preflight(engine, request)
            if error:
                results[engine] = error
                continue
            cached = self.cache.get(engine, request)
            if cached:
                results[engine] = cached
                continue
            now = time.monotonic()
            expires = min(deadline, now + self.config["request_timeout_seconds"])
            if expires <= now:
                results[engine] = failure(engine, request, "network", "已达到本次查询的总时间限制")
                continue
            pending[engine] = expires
            threading.Thread(target=worker, args=(engine, expires - now, now), daemon=True).start()

        while pending:
            now = time.monotonic()
            for engine, expires in list(pending.items()):
                if now >= expires:
                    results[engine] = failure(engine, request, "network", "请求超时，请稍后重试")
                    del pending[engine]
            if not pending:
                break
            try:
                engine, result, completed, started = inbox.get(timeout=max(0.001, min(pending.values()) - time.monotonic()))
            except queue.Empty:
                continue
            if engine not in pending or completed > pending[engine]:
                continue
            del pending[engine]
            result.elapsed_ms = round((completed - started) * 1000)
            results[engine] = result
            self.cache.put(request, result)

        ordered = [results[engine] for engine in engines]
        for result in ordered:
            self.logger.info("engine=%s status=%s http=%s vendor=%s request_id=%s elapsed_ms=%s cached=%s",
                             result.engine, result.error_code, result.http_status, result.vendor_code,
                             result.request_id, result.elapsed_ms, result.cached)
        return ordered

    def translate(self, query: Query) -> list[TranslationResult]:
        deadline = time.monotonic() + self.config["query_timeout_seconds"]
        if query.all_engines:
            # 顺序由配置决定，与网络返回先后无关；未列入优先级的新增适配器补在后面。
            order = list(dict.fromkeys(self.config["fallback_order"] + list(self.adapters)))
            engines = [engine for engine in order if self.config["engines"].get(engine, {}).get("enable")]
            return self.execute(engines, query.request, deadline) if engines else [
                failure("", query.request, "config", "没有启用的引擎，请运行 trconfig")]
        first = query.engine or self.config["default_engine"]
        if query.engine or not self.config["fallback_enable"]:
            return self.execute([first], query.request, deadline)
        attempts = 0
        failures = []
        order = list(dict.fromkeys([first] + self.config["fallback_order"]))
        for engine in order:
            preflight = self.preflight(engine, query.request)
            if preflight and preflight.error_code in {"config", "disabled"}:
                failures.append(preflight)
                continue
            result = self.execute([engine], query.request, deadline)[0]
            attempts += 1
            if result.success:
                result.fallback_from = first if engine != first else ""
                return [result]
            failures.append(result)
            if result.error_code not in FALLBACK_ERRORS or attempts >= 3 or time.monotonic() >= deadline:
                break
        return failures or [failure(first, query.request, "config")]
