"""Тесты оркестрации с замоканным поиском (без реальной сети)."""

from plagiarism.cache import Cache
from plagiarism.engine import check_text
from plagiarism.providers import MockProvider, RateLimitError, Result, SearchProvider


# «Донорский» текст, который якобы лежит в интернете.
BORROWED = (
    "Искусственный интеллект стремительно меняет современную экономику и "
    "производство по всему миру уже сегодня заметно."
)
ORIGINAL = (
    "Ромашки цвели на далёком лугу пока рыжий кот дремал под старым деревянным "
    "крыльцом старого дома у реки."
)


def _corpus():
    return [{"title": "Статья про ИИ", "url": "https://journal.example.com/ai", "text": BORROWED}]


def _text():
    # один абзац дословно заимствован, второй оригинальный
    return BORROWED + " " + ORIGINAL


def test_percent_and_sources(tmp_path):
    cache = Cache(str(tmp_path / "c.json"))
    provider = MockProvider(_corpus())
    report = check_text(_text(), provider, cache=cache, threshold=80, delay=0)

    assert report.total_fragments >= 2
    assert report.matched_fragments, "заимствованный фрагмент должен найтись"
    assert report.percent > 0
    top = report.top_sources()
    assert top and top[0].domain == "journal.example.com"


def test_cache_prevents_second_network_hit(tmp_path):
    cache_path = str(tmp_path / "c.json")
    provider = MockProvider(_corpus())

    r1 = check_text(_text(), provider, cache=Cache(cache_path), threshold=80, delay=0)
    assert r1.queries_used > 0
    assert r1.cache_hits == 0

    # второй запуск: всё берётся из кэша, сеть не трогаем
    r2 = check_text(_text(), provider, cache=Cache(cache_path), threshold=80, delay=0)
    assert r2.queries_used == 0
    assert r2.cache_hits == r2.total_fragments
    assert r2.percent == r1.percent


def test_max_queries_stops_early_and_resumes(tmp_path):
    cache_path = str(tmp_path / "c.json")
    provider = MockProvider(_corpus())

    # разрешаем лишь 1 сетевой запрос — проверка должна остановиться досрочно
    r1 = check_text(_text(), provider, cache=Cache(cache_path), threshold=80,
                    max_queries=1, delay=0)
    assert r1.queries_used == 1
    assert r1.stopped_early
    assert r1.remaining > 0

    # докрутка: кэш отдаёт уже проверенное, лимит тратится только на новое
    r2 = check_text(_text(), provider, cache=Cache(cache_path), threshold=80,
                    max_queries=100, delay=0)
    assert r2.cache_hits >= 1
    assert not r2.stopped_early
    assert r2.remaining == 0


class _RateLimitedProvider(SearchProvider):
    name = "mock"
    free_limit = 100

    def search(self, query):
        raise RateLimitError("mock 429")


def test_rate_limit_stops_gracefully(tmp_path):
    cache = Cache(str(tmp_path / "c.json"))
    report = check_text(_text(), _RateLimitedProvider(), cache=cache, threshold=80, delay=0)
    assert report.stopped_early
    assert "429" in report.stop_reason or "лимит" in report.stop_reason
    assert report.percent == 0.0  # ничего не проверено — деления на ноль нет
