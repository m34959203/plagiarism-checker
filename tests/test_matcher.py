"""Юнит-тесты матчинга и агрегации (без сети)."""

from plagiarism.matcher import domain_of, match_results, similarity
from plagiarism.providers import Result
from plagiarism.text import Shingle


def test_domain_of_strips_www():
    assert domain_of("https://www.example.com/path?q=1") == "example.com"
    assert domain_of("http://sub.foo.org/") == "sub.foo.org"
    assert domain_of("not a url") == ""


def test_similarity_exact_high():
    phrase = "быстрая бурая лиса прыгает через ленивую собаку каждый день"
    assert similarity(phrase, "...быстрая бурая лиса прыгает через ленивую собаку каждый день...") >= 95
    assert similarity(phrase, "совершенно другой несвязанный текст про погоду") < 60


def test_match_results_threshold():
    sh = Shingle(text="машинное обучение меняет индустрию разработки программного обеспечения",
                 start=0, end=10)
    results = [
        Result(title="A", url="https://a.com/x",
                snippet="машинное обучение меняет индустрию разработки программного обеспечения"),
        Result(title="B", url="https://b.com/y", snippet="рецепт борща и другие блюда"),
    ]
    matched = match_results(sh, results, threshold=80)
    assert len(matched) == 1
    assert matched[0].domain == "a.com"
    assert matched[0].score >= 80


def test_match_results_sorted_desc():
    sh = Shingle(text="один два три четыре пять шесть семь восемь", start=0, end=10)
    results = [
        # частичное совпадение с посторонними словами -> ниже балл
        Result(title="low", url="https://low.com",
                snippet="один два три погода солнце ветер дождь завтра прогноз"),
        # дословное совпадение -> максимум
        Result(title="high", url="https://high.com",
                snippet="один два три четыре пять шесть семь восемь"),
    ]
    matched = match_results(sh, results, threshold=0)
    assert len(matched) == 2
    assert matched[0].domain == "high.com"        # лучший — первым
    assert matched[0].score >= matched[1].score
