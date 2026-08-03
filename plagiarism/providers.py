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

    def __init__(self, api_key: str, *, top_k: int = 5, timeout: int = 20):
        if not api_key:
            raise ProviderError(
                "Не задан SERPER_API_KEY. Получите ключ на https://serper.dev "
                "и добавьте его в .env (см. .env.example)."
            )
        self.api_key = api_key
        self.top_k = top_k
        self.timeout = timeout

    def search(self, query: str) -> list[Result]:
        _require_requests()
        try:
            resp = requests.post(
                self.ENDPOINT,
                headers={"X-API-KEY": self.api_key, "Content-Type": "application/json"},
                json={"q": self.exact(query), "num": self.top_k},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:  # pragma: no cover - сеть
            raise ProviderError(f"Сетевая ошибка serper: {exc}") from exc
        if resp.status_code == 429:
            raise RateLimitError("serper: превышен лимит запросов (429)")
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

def get_provider(name: str, env: dict | None = None, *, top_k: int = 5) -> SearchProvider:
    """Создать провайдера по имени, читая ключи из ``env`` (по умолчанию os.environ)."""
    env = dict(os.environ if env is None else env)
    name = (name or "serper").lower()
    if name == "serper":
        return SerperProvider(env.get("SERPER_API_KEY", ""), top_k=top_k)
    if name == "google":
        return GoogleCSEProvider(
            env.get("GOOGLE_API_KEY", ""), env.get("GOOGLE_CSE_ID", ""), top_k=top_k
        )
    if name == "brave":
        return BraveProvider(env.get("BRAVE_API_KEY", ""), top_k=top_k)
    if name == "mock":
        return MockProvider(top_k=top_k)
    raise ProviderError(f"Неизвестный провайдер: {name!r} (serper|google|brave|mock)")
