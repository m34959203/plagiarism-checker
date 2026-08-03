"""Нормализация текста, разбивка на предложения и построение шинглов.

Шингл (shingle) — окно из нескольких подряд идущих слов, которое затем ищется
в интернете как точная фраза. Каждый шингл несёт свои координаты (start/end)
в *нормализованном* тексте — это позволяет и подсвечивать совпадения в отчёте,
и считать процент заимствований по объёму символов, а не по числу запросов.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# --- нормализация -----------------------------------------------------------

# Типографские кавычки/тире -> простые ASCII, чтобы фраза для поиска была стабильной.
_QUOTES = {
    "«": '"', "»": '"',            # « »
    "“": '"', "”": '"',            # “ ”
    "„": '"', "‟": '"',
    "‘": "'", "’": "'",            # ‘ ’
    "–": "-", "—": "-", "―": "-",  # – — ―
    " ": " ",                            # неразрывный пробел
    "…": "...",                          # …
}
_QUOTE_RE = re.compile("|".join(re.escape(k) for k in _QUOTES))
_WS_RE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Привести кавычки/тире к ASCII и схлопнуть пробелы. Границы предложений
    (переводы строк между абзацами) при этом теряются намеренно — для поиска
    нам важен непрерывный поток слов."""
    text = _QUOTE_RE.sub(lambda m: _QUOTES[m.group(0)], text)
    text = _WS_RE.sub(" ", text)
    return text.strip()


# --- разбивка на предложения ------------------------------------------------

# Распространённые сокращения, после которых точка НЕ завершает предложение.
_ABBREVIATIONS = {
    # ru
    "т", "тт", "др", "пр", "см", "рис", "табл", "стр", "гл", "п", "пп", "ст",
    "г", "гг", "в", "вв", "н", "э", "им", "проф", "доц", "акад", "чл", "корр",
    "руб", "коп", "тыс", "млн", "млрд", "ул", "д", "кв", "обл", "респ",
    # en
    "mr", "mrs", "ms", "dr", "prof", "vs", "etc", "e", "g", "i", "fig", "eq",
    "no", "vol", "pp", "al",
}

# Кандидат на конец предложения: .!? (возможно повторённые), затем пробел и
# заглавная буква / цифра / кавычка — типичное начало новой фразы.
_SENT_SPLIT_RE = re.compile(
    r'(?<=[.!?])["\')\]]?\s+(?=["\'(\[]?[A-ZА-ЯЁ0-9])'
)
_WORD_BEFORE_DOT_RE = re.compile(r"(\w+)\.$")


@dataclass
class Sentence:
    text: str
    start: int  # смещение в нормализованном тексте
    end: int


def split_sentences(text: str) -> list[Sentence]:
    """Разбить нормализованный текст на предложения с сохранением координат.

    Ложные разрывы после сокращений («и т. д.», «рис. 2») отсеиваются проверкой
    слова перед точкой по словарю ``_ABBREVIATIONS``.
    """
    sentences: list[Sentence] = []
    pos = 0
    # Идём по кандидатам-разрывам; между ними — куски-предложения.
    last = 0
    for m in _SENT_SPLIT_RE.finditer(text):
        chunk = text[last:m.start()]
        wb = _WORD_BEFORE_DOT_RE.search(chunk.rstrip())
        if wb and wb.group(1).lower() in _ABBREVIATIONS:
            # это сокращение, а не конец предложения — не разрываем
            continue
        _append_sentence(sentences, text, last, m.start())
        last = m.end()
    _append_sentence(sentences, text, last, len(text))
    return sentences


def _append_sentence(acc: list[Sentence], text: str, start: int, end: int) -> None:
    raw = text[start:end]
    stripped = raw.strip()
    if not stripped:
        return
    # скорректировать координаты на обрезанные пробелы
    lead = len(raw) - len(raw.lstrip())
    real_start = start + lead
    acc.append(Sentence(text=stripped, start=real_start, end=real_start + len(stripped)))


# --- шинглы -----------------------------------------------------------------

_WORD_RE = re.compile(r"\S+")


@dataclass
class Shingle:
    """Окно из нескольких слов, которое ищется как точная фраза."""
    text: str
    start: int          # смещение начала в нормализованном тексте
    end: int            # смещение конца
    key: str = field(default="", compare=False)  # нормализованный ключ для дедупа

    def __post_init__(self) -> None:
        if not self.key:
            self.key = _phrase_key(self.text)


def _phrase_key(phrase: str) -> str:
    return re.sub(r"[^\wа-яё]+", " ", phrase.lower()).strip()


def make_shingles(
    text: str,
    *,
    min_words: int = 8,
    max_words: int = 12,
    min_sentence_words: int = 6,
    windows_per_sentence: int = 2,
) -> list[Shingle]:
    """Построить список уникальных шинглов из нормализованного текста.

    Для каждого предложения:
      * если слов < ``min_sentence_words`` — пропускаем (шум);
      * иначе берём до ``windows_per_sentence`` окон длиной ``max_words``
        (или короче для коротких предложений, но не меньше ``min_words``),
        равномерно распределённых по предложению.

    Пересекающиеся/повторяющиеся фразы отбрасываются по нормализованному ключу.
    """
    if min_words > max_words:
        min_words = max_words
    shingles: list[Shingle] = []
    seen: set[str] = set()

    for sent in split_sentences(text):
        words = [
            (m.group(0), sent.start + m.start(), sent.start + m.end())
            for m in _WORD_RE.finditer(sent.text)
        ]
        n = len(words)
        if n < min_sentence_words:
            continue

        win = min(max_words, n)
        if win < min_words and n >= min_words:
            win = min_words
        win = min(win, n)

        # позиции начала окон, равномерно по предложению
        starts = _window_starts(n, win, windows_per_sentence)
        for s in starts:
            e = min(s + win, n)
            if e - s < min(min_words, n):
                continue
            first = words[s]
            last = words[e - 1]
            phrase = text[first[1]:last[2]]
            key = _phrase_key(phrase)
            if not key or key in seen:
                continue
            seen.add(key)
            shingles.append(Shingle(text=phrase, start=first[1], end=last[2], key=key))

    return shingles


def _window_starts(n_words: int, win: int, count: int) -> list[int]:
    """Начальные индексы для ``count`` окон длиной ``win`` в предложении из
    ``n_words`` слов, максимально разнесённые и не выходящие за границы."""
    max_start = max(0, n_words - win)
    if count <= 1 or max_start == 0:
        return [0]
    if count == 2:
        return sorted({0, max_start})
    step = max_start / (count - 1)
    return sorted({round(i * step) for i in range(count)})
