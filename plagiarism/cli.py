"""Интерфейс командной строки.

    python -m plagiarism check input.txt --provider serper --threshold 80 \\
        --max-queries 200 --report report.html
"""

from __future__ import annotations

import argparse
import sys

from . import __version__
from .cache import Cache
from .engine import Progress, check_text
from .providers import ProviderError, get_provider
from .report import render_console, render_html

MIN_TEXT_LEN = 10_000


def _load_env() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass  # без python-dotenv просто читаем реальное окружение


def _read_input(args) -> str:
    if args.stdin:
        return sys.stdin.read()
    if not args.input:
        raise SystemExit("Укажите путь к файлу или флаг --stdin")
    try:
        with open(args.input, "r", encoding="utf-8") as fh:
            return fh.read()
    except OSError as exc:
        raise SystemExit(f"Не удалось прочитать файл {args.input!r}: {exc}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="plagiarism",
        description="Проверка текста на плагиат: поиск дословных заимствований в интернете.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    chk = sub.add_parser("check", help="проверить текст на заимствования",
                         formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    chk.add_argument("input", nargs="?", help="путь к .txt/.md файлу с текстом")
    chk.add_argument("--stdin", action="store_true", help="читать текст из stdin вместо файла")
    chk.add_argument("--provider", default="serper", choices=["serper", "google", "brave", "mock"],
                     help="поисковый провайдер")
    chk.add_argument("--threshold", type=float, default=80.0,
                     help="порог схожести фразы и сниппета (0..100)")
    chk.add_argument("--max-queries", type=int, default=None,
                     help="жёсткий потолок сетевых запросов за запуск (по умолчанию — лимит провайдера)")
    chk.add_argument("--delay", type=float, default=1.0, help="пауза между запросами, сек")
    chk.add_argument("--fetch-source", action="store_true",
                     help="скачивать страницы-источники для подтверждения (медленнее)")
    chk.add_argument("--cache", default=".plagiarism_cache.json", help="файл дискового кэша")
    chk.add_argument("--report", metavar="FILE", help="сохранить HTML-отчёт в файл")
    chk.add_argument("--min-words", type=int, default=8, help="мин. длина окна-шингла в словах")
    chk.add_argument("--max-words", type=int, default=12, help="макс. длина окна-шингла в словах")
    chk.add_argument("--allow-short", action="store_true",
                     help="не требовать минимум 10 000 символов")
    chk.add_argument("--verbose", "-v", action="store_true", help="лог по каждому запросу")
    return parser


def _make_progress(verbose: bool):
    def cb(p: Progress) -> None:
        if verbose:
            mark = "＋" if p.matched else "·"
            src = "cache" if p.source == "cache" else ("NET" if p.source == "network" else "err")
            phrase = (p.phrase[:60] + "…") if len(p.phrase) > 60 else p.phrase
            print(f"  [{p.index:>4}/{p.total}] {mark} ({src:>5}) q={p.queries_used}  {phrase}",
                  file=sys.stderr)
        elif p.index % 10 == 0 or p.index == p.total:
            pct = int(p.index / p.total * 100) if p.total else 100
            print(f"\r  прогресс: {p.index}/{p.total} ({pct}%)  запросов: {p.queries_used}   ",
                  end="", file=sys.stderr, flush=True)
    return cb


def cmd_check(args) -> int:
    _load_env()
    text = _read_input(args)

    if len(text) < MIN_TEXT_LEN and not args.allow_short:
        print(
            f"⚠ Текст короткий ({len(text)} симв.). Ожидается от {MIN_TEXT_LEN}. "
            f"Используйте --allow-short, чтобы всё равно проверить.",
            file=sys.stderr,
        )
        return 2

    try:
        provider = get_provider(args.provider)
    except ProviderError as exc:
        print(f"Ошибка провайдера: {exc}", file=sys.stderr)
        return 3

    cache = Cache(args.cache)

    report = check_text(
        text,
        provider,
        cache=cache,
        threshold=args.threshold,
        max_queries=args.max_queries,
        delay=args.delay,
        fetch_source=args.fetch_source,
        min_words=args.min_words,
        max_words=args.max_words,
        progress_cb=_make_progress(args.verbose),
    )
    print("", file=sys.stderr)  # завершить строку прогресса
    print(render_console(report))

    if args.report:
        try:
            with open(args.report, "w", encoding="utf-8") as fh:
                fh.write(render_html(report))
            print(f"\nHTML-отчёт сохранён: {args.report}")
        except OSError as exc:
            print(f"Не удалось записать отчёт: {exc}", file=sys.stderr)
            return 4

    return 0


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "check":
        return cmd_check(args)
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
