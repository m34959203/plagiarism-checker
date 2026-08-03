"""Юнит-тесты разбивки текста и построения шинглов (без сети)."""

from plagiarism.text import (
    Shingle,
    make_shingles,
    normalize,
    split_sentences,
)


def test_normalize_quotes_and_spaces():
    src = "«Привет»   —  это\n\nтест… “ok”"
    out = normalize(src)
    assert "«" not in out and "»" not in out
    assert "—" not in out
    assert "…" not in out and "..." in out
    assert "  " not in out            # пробелы схлопнуты
    assert out.startswith('"Привет"')


def test_split_basic_sentences():
    text = normalize("Первое предложение. Второе предложение! Третье?")
    sents = split_sentences(text)
    assert len(sents) == 3
    assert sents[0].text == "Первое предложение."
    # координаты указывают на исходный нормализованный текст
    for s in sents:
        assert text[s.start:s.end] == s.text


def test_split_respects_abbreviations():
    text = normalize("Мы взяли яблоки, груши и т. д. и пошли домой. Конец.")
    sents = split_sentences(text)
    # "и т. д." не должно разрывать предложение
    assert len(sents) == 2
    assert "т. д." in sents[0].text


def test_shingles_word_bounds_and_dedup():
    words = " ".join(f"слово{i}" for i in range(30)) + "."
    shingles = make_shingles(words, min_words=8, max_words=12, windows_per_sentence=2)
    assert shingles, "должен получиться хотя бы один шингл"
    for sh in shingles:
        n = len(sh.text.split())
        assert 8 <= n <= 12
    # дедуп по ключу
    keys = [sh.key for sh in shingles]
    assert len(keys) == len(set(keys))


def test_short_sentences_skipped():
    long_sent = "Далее следует достаточно длинное предложение " + " ".join(
        f"слово{i}" for i in range(20)
    )
    text = "Кот спит. " + long_sent + "."
    shingles = make_shingles(text, min_sentence_words=6)
    # первое предложение из 2 слов должно быть пропущено
    assert all("Кот спит" not in sh.text for sh in shingles)


def test_shingle_offsets_map_back():
    text = normalize(" ".join(f"w{i}" for i in range(40)) + ".")
    for sh in make_shingles(text):
        assert text[sh.start:sh.end] == sh.text
