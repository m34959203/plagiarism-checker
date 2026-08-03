"""Оркестрация проверки: обход шинглов, поиск, учёт лимита и докрутка из кэша.

Ключевые свойства:
  * дисковый кэш — повторные запуски не тратят лимит на уже проверенные фразы;
  * жёсткий потолок числа сетевых запросов (``max_queries``);
  * корректная остановка при 429 / достижении лимита с сохранением кэша;
  * экспоненциальный backoff при 429/5xx;
  * процент заимствований считается по ОБЪЁМУ символов совпавших фрагментов,
    а не по доле запросов.
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable, Optional

from . import text as textmod
from .cache import Cache
from .matcher import Match, best_match, domain_of, match_results
from .providers import ProviderError, RateLimitError, Result, SearchProvider

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None  # type: ignore


ProgressCb = Callable[["Progress"], None]


@dataclass
class Progress:
    index: int          # сколько шинглов обработано
    total: int          # всего шинглов
    phrase: str
    source: str         # 'cache' | 'network'
    matched: bool
    queries_used: int


@dataclass
class FragmentResult:
    shingle: textmod.Shingle
    matches: list[Match] = field(default_factory=list)
    checked: bool = False        # фраза действительно проверена (кэш или сеть)
    from_cache: bool = False

    @property
    def matched(self) -> bool:
        return bool(self.matches)

    @property
    def best(self) -> Optional[Match]:
        return best_match(self.matches)


@dataclass
class SourceStat:
    domain: str
    urls: set = field(default_factory=set)
    fragments: int = 0
    max_score: float = 0.0
    example_url: str = ""
    title: str = ""


@dataclass
class Report:
    provider: str
    threshold: float
    normalized_text: str
    fragments: list[FragmentResult]
    queries_used: int
    cache_hits: int
    stopped_early: bool
    stop_reason: str = ""

    # --- агрегаты (считаются лениво) ---
    @property
    def checked_fragments(self) -> list[FragmentResult]:
        return [f for f in self.fragments if f.checked]

    @property
    def matched_fragments(self) -> list[FragmentResult]:
        return [f for f in self.fragments if f.matched]

    @property
    def checked_chars(self) -> int:
        return sum(len(f.shingle.text) for f in self.checked_fragments)

    @property
    def matched_chars(self) -> int:
        return sum(len(f.shingle.text) for f in self.matched_fragments)

    @property
    def percent(self) -> float:
        cc = self.checked_chars
        return round(self.matched_chars / cc * 100, 1) if cc else 0.0

    @property
    def originality(self) -> float:
        return round(100.0 - self.percent, 1)

    @property
    def total_fragments(self) -> int:
        return len(self.fragments)

    @property
    def remaining(self) -> int:
        return sum(1 for f in self.fragments if not f.checked)

    def top_sources(self, limit: int = 5) -> list[SourceStat]:
        by_domain: dict[str, SourceStat] = {}
        for frag in self.matched_fragments:
            for m in frag.matches:
                st = by_domain.get(m.domain)
                if st is None:
                    st = SourceStat(domain=m.domain, example_url=m.url, title=m.title)
                    by_domain[m.domain] = st
                st.urls.add(m.url)
                st.max_score = max(st.max_score, m.score)
            # фрагмент засчитываем домену лучшего совпадения
            b = frag.best
            if b:
                by_domain[b.domain].fragments += 1
        stats = sorted(by_domain.values(), key=lambda s: (s.fragments, s.max_score), reverse=True)
        return stats[:limit]


def _fetch_source_contains(url: str, phrase: str, threshold: float, timeout: int = 15) -> bool:
    """Скачать страницу и проверить наличие фразы в её тексте (подтверждение)."""
    if requests is None:
        return True
    try:
        from bs4 import BeautifulSoup
    except ImportError:  # pragma: no cover
        BeautifulSoup = None  # type: ignore
    try:
        resp = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0 (plagiarism-checker)"})
        if resp.status_code != 200:
            return False
        html = resp.text
    except requests.RequestException:  # pragma: no cover - сеть
        return False
    if BeautifulSoup is not None:
        body = BeautifulSoup(html, "html.parser").get_text(" ")
    else:  # грубый фолбэк без bs4
        import re
        body = re.sub(r"<[^>]+>", " ", html)
    from rapidfuzz import fuzz
    return fuzz.partial_ratio(phrase.lower(), body.lower()) >= threshold


def check_text(
    raw_text: str,
    provider: SearchProvider,
    *,
    cache: Cache,
    threshold: float = 80.0,
    max_queries: Optional[int] = None,
    delay: float = 1.0,
    fetch_source: bool = False,
    max_retries: int = 3,
    min_words: int = 8,
    max_words: int = 12,
    progress_cb: Optional[ProgressCb] = None,
) -> Report:
    """Проверить текст на заимствования. См. модульную docstring."""
    norm = textmod.normalize(raw_text)
    shingles = textmod.make_shingles(norm, min_words=min_words, max_words=max_words)

    if max_queries is None:
        max_queries = provider.free_limit

    fragments = [FragmentResult(shingle=sh) for sh in shingles]
    queries_used = 0
    cache_hits = 0
    stopped_early = False
    stop_reason = ""

    for i, frag in enumerate(fragments):
        phrase = frag.shingle.text

        # 1) кэш — не тратит лимит
        cached = cache.get(provider.name, phrase)
        if cached is not None:
            frag.matches = match_results(frag.shingle, cached, threshold)
            frag.checked = True
            frag.from_cache = True
            cache_hits += 1
            _emit(progress_cb, i + 1, len(fragments), phrase, "cache", frag.matched, queries_used)
            continue

        # 2) достигнут потолок запросов — останавливаемся, остальное на следующий раз
        if queries_used >= max_queries:
            stopped_early = True
            stop_reason = f"достигнут лимит --max-queries={max_queries}"
            break

        # 3) сетевой запрос с backoff по 429/5xx
        try:
            results = _search_with_backoff(provider, phrase, max_retries=max_retries, delay=delay)
        except RateLimitError as exc:
            stopped_early = True
            stop_reason = f"провайдер вернул лимит: {exc}"
            break
        except ProviderError as exc:
            # разовую ошибку по фразе не считаем фатальной — пропускаем фразу
            _emit(progress_cb, i + 1, len(fragments), phrase, "error", False, queries_used)
            frag.checked = False
            continue

        queries_used += 1
        cache.set(provider.name, phrase, results)

        matches = match_results(frag.shingle, results, threshold)
        if matches and fetch_source:
            matches = [m for m in matches if _fetch_source_contains(m.url, phrase, threshold)]
        frag.matches = matches
        frag.checked = True
        _emit(progress_cb, i + 1, len(fragments), phrase, "network", frag.matched, queries_used)

        if delay and i + 1 < len(fragments):
            time.sleep(delay)

    cache.save()

    return Report(
        provider=provider.name,
        threshold=threshold,
        normalized_text=norm,
        fragments=fragments,
        queries_used=queries_used,
        cache_hits=cache_hits,
        stopped_early=stopped_early,
        stop_reason=stop_reason,
    )


def _search_with_backoff(
    provider: SearchProvider, phrase: str, *, max_retries: int, delay: float
) -> list[Result]:
    attempt = 0
    while True:
        try:
            return provider.search(phrase)
        except RateLimitError:
            # 429 пробрасываем наверх сразу — это сигнал остановиться
            raise
        except ProviderError:
            attempt += 1
            if attempt > max_retries:
                raise
            time.sleep(min(delay * (2 ** attempt), 30))


def _emit(cb, index, total, phrase, source, matched, queries_used):
    if cb is not None:
        cb(Progress(index=index, total=total, phrase=phrase,
                    source=source, matched=matched, queries_used=queries_used))
