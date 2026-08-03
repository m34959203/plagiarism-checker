"""Оценка схожести искомой фразы с найденными сниппетами.

Используется ``rapidfuzz.token_set_ratio`` — устойчивый к перестановкам слов и
лишним «хвостам» сниппета показатель (0..100). Фраза считается заимствованной,
если хотя бы один результат набирает не меньше порога ``threshold``.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from rapidfuzz import fuzz

from .providers import Result
from .text import Shingle


@dataclass
class Match:
    """Совпадение фразы с конкретным источником."""
    url: str
    domain: str
    title: str
    snippet: str
    score: float


def domain_of(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def similarity(phrase: str, snippet: str) -> float:
    """Схожесть фразы и сниппета, 0..100."""
    if not phrase or not snippet:
        return 0.0
    return float(fuzz.token_set_ratio(phrase, snippet))


def match_results(
    shingle: Shingle, results: list[Result], threshold: float
) -> list[Match]:
    """Вернуть отсортированные по убыванию совпадения (score >= threshold)."""
    matches: list[Match] = []
    for r in results:
        score = similarity(shingle.text, r.snippet)
        if score >= threshold:
            matches.append(
                Match(
                    url=r.url,
                    domain=domain_of(r.url),
                    title=r.title,
                    snippet=r.snippet,
                    score=round(score, 1),
                )
            )
    matches.sort(key=lambda m: m.score, reverse=True)
    return matches


def best_match(matches: list[Match]) -> Match | None:
    return matches[0] if matches else None
