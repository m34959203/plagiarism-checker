"""Демо-прогон без сети: собирает текст >10 000 символов, часть которого
дословно «заимствована» из мок-корпуса, и прогоняет проверку MockProvider'ом.

    python scripts/demo.py

Печатает консольный отчёт и сохраняет report.html. Служит и как smoke-тест
всей цепочки: text -> providers -> matcher -> engine -> report.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from plagiarism.cache import Cache
from plagiarism.engine import check_text
from plagiarism.providers import MockProvider
from plagiarism.report import render_console, render_html

# «Донорские» документы, будто найденные в интернете.
DONORS = [
    {
        "title": "Википедия — Искусственный интеллект",
        "url": "https://ru.wikipedia.org/wiki/Искусственный_интеллект",
        "text": (
            "Искусственный интеллект это свойство интеллектуальных систем выполнять "
            "творческие функции которые традиционно считаются прерогативой человека. "
            "Машинное обучение является подразделом искусственного интеллекта изучающим "
            "методы построения алгоритмов способных обучаться на данных и улучшать свои "
            "показатели по мере накопления опыта без явного программирования."
        ),
    },
    {
        "title": "Хабр — Нейронные сети",
        "url": "https://habr.com/ru/articles/neural-networks",
        "text": (
            "Глубокие нейронные сети состоят из множества последовательных слоёв "
            "нейронов и способны автоматически выделять сложные признаки из сырых "
            "данных таких как изображения звук и текст что сделало их основой "
            "современных систем распознавания и генерации."
        ),
    },
]

# Заимствованные абзацы — дословные фрагменты доноров.
BORROWED_1 = DONORS[0]["text"]
BORROWED_2 = DONORS[1]["text"]

# Оригинальный «наполнитель», чтобы текст был длинным и разбавленным.
ORIGINAL_FILLER = (
    "В нашем небольшом исследовании мы решили посмотреть на вопрос под несколько "
    "другим углом и собрали собственные наблюдения за поведением студентов во время "
    "весенней сессии в маленьком университете у подножия холмов. Погода стояла "
    "переменчивая, и это заметно влияло на посещаемость утренних пар, о чём "
    "свидетельствуют наши скромные подсчёты, сделанные вручную на полях тетради. "
)


def build_text() -> str:
    parts = [BORROWED_1]
    # разбавляем оригинальным текстом до >10k символов
    while sum(len(p) for p in parts) < 10500:
        parts.append(ORIGINAL_FILLER)
    parts.append(BORROWED_2)
    parts.append(ORIGINAL_FILLER)
    return "\n\n".join(parts)


def main() -> int:
    text = build_text()
    print(f"Длина тестового текста: {len(text)} символов\n")

    provider = MockProvider(DONORS)
    cache = Cache(os.path.join(os.path.dirname(__file__), "demo_cache.json"))

    report = check_text(text, provider, cache=cache, threshold=80, delay=0)
    print(render_console(report))

    out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "report.html")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(render_html(report))
    print(f"\nHTML-отчёт: {out}")

    # простая самопроверка ожиданий демо
    assert report.matched_fragments, "демо: заимствования должны найтись"
    assert report.percent > 0, "демо: процент должен быть > 0"
    print(f"\nOK · заимствовано {report.percent:.1f}% · источников: {len(report.top_sources())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
