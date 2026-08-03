"""Абстракция поискового провайдера и его реализации.

Все провайдеры возвращают единый список :class:`Result`. Выбор провайдера —
через фабрику :func:`get_provider`. Ключи читаются из окружения (``.env``),
хардкодить их нельзя.

Провайдеры:
    serper — serper.dev, 2500 бесплатных запросов единоразово (по умолчанию)
    google — Google Custom Search JSON API, 100 запросов/день
    brave  — Brave Search API, ~2000 запросов/мес
    mock   — оффлайн-провайдер для тестов и демо (без сети)
"""

from __future__ import annotations

import os
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass

try:  # requests не нужен для mock-провайдера и тестов
    import requests
except ImportError:  # pragma: no cover
    requests = None  # type: ignore


@dataclass
class Result:
    """Единичный результат поиска."""
    title: str
    url: str
    snippet: str


class ProviderError(RuntimeError):
    """Общая ошибка провайдера (сеть, некорректный ответ, отсутствие ключа)."""


class RateLimitError(ProviderError):
    """Достигнут лимит запросов (HTTP 429) — повод корректно остановиться."""


class SearchProvider(ABC):
    """Базовый класс поискового провайдера."""

    name: str = "base"
    #: рекомендуемый бесплатный потолок запросов — дефолт для --max-queries
    free_limit: int = 100

    @abstractmethod
    def search(self, query: str) -> list[Result]:
        """Вернуть до нескольких результатов по точной фразе ``query``.

        Реализация должна бросать :class:`RateLimitError` на 429 и
        :class:`ProviderError` на прочих сбоях.
        """
        raise NotImplementedError

    @staticmethod
    def exact(query: str) -> str:
        """Обернуть фразу в кавычки для режима точного совпадения."""
        q = query.strip().strip('"')
        return f'"{q}"'


def _require_requests() -> None:
    if requests is None:  # pragma: no cover
        raise ProviderError(
            "Библиотека 'requests' не установлена. Выполните: pip install requests"
        )


# --- serper.dev -------------------------------------------------------------

class SerperProvider(SearchProvider):
    name = "serper"
    free_limit = 2500
    ENDPOINT = "https://google.serper.dev/search"

    #: базовый бесплатный лимит одного аккаунта serper
    PER_KEY_FREE = 2500

    def __init__(self, api_key, *, top_k: int = 5, timeout: int = 20):
        """``api_key`` — строка или список строк. Список ключей образует пул:
        при исчерпании лимита (429) провайдер сам переключается на следующий
        ключ, а RateLimitError бросается только когда исчерпаны ВСЕ ключи."""
        keys = api_key if isinstance(api_key, (list, tuple)) else [api_key]
        self.keys = [str(k).strip() for k in keys if k and str(k).strip()]
        if not self.keys:
            raise ProviderError(
                "Не задан SERPER_API_KEY. Получите ключ на https://serper.dev "
                "и добавьте его в .env (см. .env.example). Для пула — SERPER_API_KEYS."
            )
        self.top_k = top_k
        self.timeout = timeout
        self._idx = 0
        self._exhausted: set[int] = set()
        # общий бесплатный запас растёт пропорционально числу ключей
        self.free_limit = self.PER_KEY_FREE * len(self.keys)

    @property
    def key_count(self) -> int:
        return len(self.keys)

    def _advance_key(self) -> bool:
        """Переключиться на следующий не исчерпанный ключ. False — если их нет."""
        for step in range(1, len(self.keys) + 1):
            j = (self._idx + step) % len(self.keys)
            if j not in self._exhausted:
                self._idx = j
                return True
        return False

    @staticmethod
    def _is_quota_error(resp) -> bool:
        if resp.status_code == 429:
            return True
        # у serper исчерпание платного/бесплатного баланса иногда приходит как 403
        if resp.status_code == 403:
            body = (resp.text or "").lower()
            return any(w in body for w in ("quota", "limit", "credit", "balance"))
        return False

    def search(self, query: str) -> list[Result]:
        _require_requests()
        while True:
            key = self.keys[self._idx]
            try:
                resp = requests.post(
                    self.ENDPOINT,
                    headers={"X-API-KEY": key, "Content-Type": "application/json"},
                    json={"q": self.exact(query), "num": self.top_k},
                    timeout=self.timeout,
                )
            except requests.RequestException as exc:  # pragma: no cover - сеть
                raise ProviderError(f"Сетевая ошибка serper: {exc}") from exc

            if self._is_quota_error(resp):
                self._exhausted.add(self._idx)
                if not self._advance_key():
                    raise RateLimitError(
                        f"serper: все {len(self.keys)} ключ(и) исчерпали лимит (429)"
                    )
                continue  # тот же запрос — следующим ключом
            if resp.status_code >= 500:
                raise ProviderError(f"serper: ошибка сервера {resp.status_code}")
            if resp.status_code != 200:
                raise ProviderError(f"serper: HTTP {resp.status_code}: {resp.text[:200]}")

            data = resp.json()
            results = []
            for item in (data.get("organic") or [])[: self.top_k]:
                results.append(
                    Result(
                        title=item.get("title", ""),
                        url=item.get("link", ""),
                        snippet=item.get("snippet", ""),
                    )
                )
            return results


# --- Google Custom Search ---------------------------------------------------

class GoogleCSEProvider(SearchProvider):
    name = "google"
    free_limit = 100
    ENDPOINT = "https://www.googleapis.com/customsearch/v1"

    def __init__(self, api_key: str, cse_id: str, *, top_k: int = 5, timeout: int = 20):
        if not api_key or not cse_id:
            raise ProviderError(
                "Для провайдера 'google' нужны GOOGLE_API_KEY и GOOGLE_CSE_ID в .env."
            )
        self.api_key = api_key
        self.cse_id = cse_id
        self.top_k = top_k
        self.timeout = timeout

    def search(self, query: str) -> list[Result]:
        _require_requests()
        try:
            resp = requests.get(
                self.ENDPOINT,
                params={
                    "key": self.api_key,
                    "cx": self.cse_id,
                    "q": self.exact(query),
                    "num": min(self.top_k, 10),
                },
                timeout=self.timeout,
            )
        except requests.RequestException as exc:  # pragma: no cover - сеть
            raise ProviderError(f"Сетевая ошибка google: {exc}") from exc
        if resp.status_code == 429:
            raise RateLimitError("google: превышен дневной лимит (429)")
        if resp.status_code == 403:
            # У Google дневной лимит часто отдаётся как 403 rateLimitExceeded
            raise RateLimitError("google: лимит исчерпан или доступ запрещён (403)")
        if resp.status_code >= 500:
            raise ProviderError(f"google: ошибка сервера {resp.status_code}")
        if resp.status_code != 200:
            raise ProviderError(f"google: HTTP {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        results = []
        for item in (data.get("items") or [])[: self.top_k]:
            results.append(
                Result(
                    title=item.get("title", ""),
                    url=item.get("link", ""),
                    snippet=item.get("snippet", ""),
                )
            )
        return results


# --- Brave Search -----------------------------------------------------------

class BraveProvider(SearchProvider):
    name = "brave"
    free_limit = 2000
    ENDPOINT = "https://api.search.brave.com/res/v1/web/search"

    def __init__(self, api_key: str, *, top_k: int = 5, timeout: int = 20):
        if not api_key:
            raise ProviderError(
                "Не задан BRAVE_API_KEY. Получите ключ на https://api.search.brave.com "
                "и добавьте его в .env."
            )
        self.api_key = api_key
        self.top_k = top_k
        self.timeout = timeout

    def search(self, query: str) -> list[Result]:
        _require_requests()
        try:
            resp = requests.get(
                self.ENDPOINT,
                headers={
                    "X-Subscription-Token": self.api_key,
                    "Accept": "application/json",
                },
                params={"q": self.exact(query), "count": self.top_k},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:  # pragma: no cover - сеть
            raise ProviderError(f"Сетевая ошибка brave: {exc}") from exc
        if resp.status_code == 429:
            raise RateLimitError("brave: превышен лимит запросов (429)")
        if resp.status_code >= 500:
            raise ProviderError(f"brave: ошибка сервера {resp.status_code}")
        if resp.status_code != 200:
            raise ProviderError(f"brave: HTTP {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        results = []
        web = (data.get("web") or {}).get("results") or []
        for item in web[: self.top_k]:
            results.append(
                Result(
                    title=item.get("title", ""),
                    url=item.get("url", ""),
                    snippet=item.get("description", ""),
                )
            )
        return results


# --- Mock (оффлайн) ---------------------------------------------------------

class MockProvider(SearchProvider):
    """Оффлайн-провайдер: не ходит в сеть.

    Инициализируется «корпусом» — списком документов вида
    ``{"title", "url", "text"}``. Для каждого запроса возвращает те документы,
    в тексте которых встречается искомая фраза (без учёта регистра), а сниппетом
    служит фрагмент вокруг совпадения. Так тестируется вся логика без API-ключа.
    """

    name = "mock"
    free_limit = 10 ** 9

    def __init__(self, corpus: list[dict] | None = None, *, top_k: int = 5):
        self.corpus = corpus or []
        self.top_k = top_k

    def search(self, query: str) -> list[Result]:
        phrase = query.strip().strip('"').lower()
        results: list[Result] = []
        for doc in self.corpus:
            body = doc.get("text", "")
            idx = body.lower().find(phrase)
            if idx == -1:
                continue
            lo = max(0, idx - 40)
            hi = min(len(body), idx + len(phrase) + 40)
            snippet = ("…" if lo > 0 else "") + body[lo:hi] + ("…" if hi < len(body) else "")
            results.append(
                Result(title=doc.get("title", "Источник"), url=doc.get("url", ""), snippet=snippet)
            )
            if len(results) >= self.top_k:
                break
        return results


# --- фабрика ----------------------------------------------------------------

def collect_serper_keys(env: dict) -> list[str]:
    """Собрать все ключи serper из окружения, без дублей и в стабильном порядке.

    Источники (все объединяются):
      * ``SERPER_API_KEY``           — одиночный ключ (идёт первым);
      * ``SERPER_API_KEYS``          — несколько ключей через запятую/пробел/перенос;
      * ``SERPER_API_KEY_2..N``      — пронумерованные ключи.
    """
    keys: list[str] = []

    def _add(raw: str) -> None:
        for k in re.split(r"[,\s]+", raw or ""):
            k = k.strip()
            if k and k not in keys:
                keys.append(k)

    _add(env.get("SERPER_API_KEY", ""))
    _add(env.get("SERPER_API_KEYS", ""))
    for i in range(2, 21):
        _add(env.get(f"SERPER_API_KEY_{i}", ""))
    return keys


def get_provider(name: str, env: dict | None = None, *, top_k: int = 5) -> SearchProvider:
    """Создать провайдера по имени, читая ключи из ``env`` (по умолчанию os.environ)."""
    env = dict(os.environ if env is None else env)
    name = (name or "serper").lower()
    if name == "serper":
        return SerperProvider(collect_serper_keys(env), top_k=top_k)
    if name == "google":
        return GoogleCSEProvider(
            env.get("GOOGLE_API_KEY", ""), env.get("GOOGLE_CSE_ID", ""), top_k=top_k
        )
    if name == "brave":
        return BraveProvider(env.get("BRAVE_API_KEY", ""), top_k=top_k)
    if name == "mock":
        return MockProvider(top_k=top_k)
    raise ProviderError(f"Неизвестный провайдер: {name!r} (serper|google|brave|mock)")
