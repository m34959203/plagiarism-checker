"""Дисковый кэш поисковых запросов.

Каждый выполненный запрос (провайдер + фраза) сохраняется в JSON-файл вместе с
результатами. При повторном запуске уже отвеченные фразы берутся из кэша и НЕ
тратят лимит API — так проверку большого текста можно доводить до конца за
несколько запусков (например, по 100 запросов в день на бесплатном тарифе).
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict

from .providers import Result


class Cache:
    def __init__(self, path: str = ".plagiarism_cache.json"):
        self.path = path
        self._data: dict[str, list[dict]] = {}
        self._dirty = False
        self.load()

    # --- ключ ---------------------------------------------------------------
    @staticmethod
    def _key(provider: str, query: str) -> str:
        return f"{provider}{query.strip().lower()}"

    # --- IO -----------------------------------------------------------------
    def load(self) -> None:
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as fh:
                    self._data = json.load(fh)
            except (json.JSONDecodeError, OSError):
                # повреждённый кэш не должен ронять проверку — начинаем с чистого
                self._data = {}
        else:
            self._data = {}

    def save(self) -> None:
        if not self._dirty:
            return
        # атомарная запись через временный файл в той же директории
        directory = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh, ensure_ascii=False, indent=0)
            os.replace(tmp, self.path)
            self._dirty = False
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    # --- доступ -------------------------------------------------------------
    def has(self, provider: str, query: str) -> bool:
        return self._key(provider, query) in self._data

    def get(self, provider: str, query: str) -> list[Result] | None:
        raw = self._data.get(self._key(provider, query))
        if raw is None:
            return None
        return [Result(**r) for r in raw]

    def set(self, provider: str, query: str, results: list[Result]) -> None:
        self._data[self._key(provider, query)] = [asdict(r) for r in results]
        self._dirty = True

    def __len__(self) -> int:
        return len(self._data)
