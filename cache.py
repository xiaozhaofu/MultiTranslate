"""有大小上限的 JSON 缓存；短锁和原子替换保护多个 Alfred 进程。"""
from __future__ import annotations

import fcntl
import json
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from models import TranslationRequest, TranslationResult
from utils import json_bytes, sha256

MAX_CACHE_BYTES = 2 * 1024 * 1024


class TranslationCache:
    def __init__(self, directory: Path, config: dict):
        self.directory = directory
        self.path = directory / "translations.json"
        self.enabled = config["cache_enabled"]
        self.ttl = config["cache_ttl_seconds"]
        self.max_text = config["cache_max_text_chars"]
        self.max_entries = config["cache_max_entries"]

    @staticmethod
    def key(engine: str, request: TranslationRequest) -> str:
        return sha256(json_bytes([1, engine, request.text, request.from_lang, request.to_lang]))

    @contextmanager
    def lock(self):
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(self.directory / "cache.lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            # 缓存竞争时降级为未命中，绝不拖住网络结果。
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                yield
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    def read(self) -> dict:
        if not self.path.exists() or self.path.stat().st_size > MAX_CACHE_BYTES:
            return {}
        try:
            values = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(values, dict):
                return {}
            now = time.time()
            return {key: value for key, value in values.items()
                    if isinstance(value, dict) and type(value.get("expires")) in (int, float)
                    and now < value["expires"] <= now + 3600}
        except (ValueError, UnicodeError):
            return {}

    def get(self, engine: str, request: TranslationRequest) -> TranslationResult | None:
        if not self.enabled or len(request.text) > self.max_text:
            return None
        try:
            with self.lock():
                entry = self.read().get(self.key(engine, request))
                if entry:
                    result = TranslationResult(**entry["result"])
                    if (result.success is True and result.error_code == "ok" and result.engine == engine
                            and result.original_text == request.text and result.to_lang == request.to_lang
                            and isinstance(result.translated_text, str) and result.translated_text.strip()):
                        result.cached = True
                        result.fallback_from = ""
                        return result
        except (OSError, ValueError, TypeError, KeyError):
            pass
        return None

    def put(self, request: TranslationRequest, result: TranslationResult) -> None:
        if not self.enabled or not result.success or len(request.text) > self.max_text:
            return
        temporary = None
        try:
            with self.lock():
                values = self.read()
                values.pop(self.key(result.engine, request), None)
                stored = result.to_dict()
                stored.update(cached=False, fallback_from="")
                values[self.key(result.engine, request)] = {"expires": time.time() + self.ttl, "result": stored}
                while len(values) > self.max_entries or len(json_bytes(values)) > MAX_CACHE_BYTES:
                    values.pop(next(iter(values)))
                with tempfile.NamedTemporaryFile(mode="wb", dir=self.directory, prefix=".cache-", delete=False) as file:
                    temporary = Path(file.name)
                    file.write(json_bytes(values))
                    file.flush()
                    os.fsync(file.fileno())
                os.replace(temporary, self.path)
        except (OSError, ValueError, TypeError):
            pass
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    # 清理失败也只能降低缓存能力，不能丢弃已经收到的译文。
                    pass
