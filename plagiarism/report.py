"""Формирование отчётов: консоль и самодостаточный HTML."""

from __future__ import annotations

import html
from datetime import datetime, timezone

from .engine import Report


# --- консоль ----------------------------------------------------------------

def render_console(report: Report) -> str:
    lines: list[str] = []
    p = report.percent
    lines.append("")
    lines.append("=" * 56)
    lines.append("  ОТЧЁТ О ПРОВЕРКЕ НА ЗАИМСТВОВАНИЯ")
    lines.append("=" * 56)
    lines.append(f"  Провайдер поиска : {report.provider}")
    lines.append(f"  Порог совпадения : {report.threshold:.0f}%")
    lines.append("-" * 56)
    lines.append(f"  ЗАИМСТВОВАНО     : {p:.1f}%")
    lines.append(f"  ОРИГИНАЛЬНОСТЬ   : {report.originality:.1f}%")
    lines.append("-" * 56)
    lines.append(f"  Проверено фрагментов : {len(report.checked_fragments)} из {report.total_fragments}")
    lines.append(f"  Совпало фрагментов   : {len(report.matched_fragments)}")
    lines.append(f"  Сетевых запросов     : {report.queries_used}")
    lines.append(f"  Взято из кэша        : {report.cache_hits}")

    top = report.top_sources(5)
    if top:
        lines.append("-" * 56)
        lines.append("  ТОП ИСТОЧНИКОВ:")
        for i, s in enumerate(top, 1):
            lines.append(f"   {i}. {s.domain or '—'}  (совпадений: {s.fragments}, макс. схожесть: {s.max_score:.0f}%)")
            lines.append(f"      {s.example_url}")

    if report.stopped_early:
        lines.append("-" * 56)
        lines.append(f"  ⚠ ОСТАНОВЛЕНО ДОСРОЧНО: {report.stop_reason}")
        lines.append(f"  Осталось непроверенных фрагментов: {report.remaining}")
        lines.append("  Запустите проверку повторно — проверенные фразы возьмутся из кэша")
        lines.append("  и не потратят лимит; дойдут очередь до оставшихся.")
    lines.append("=" * 56)
    return "\n".join(lines)


# --- HTML -------------------------------------------------------------------

def _verdict_color(percent: float) -> str:
    if percent < 15:
        return "#16a34a"   # зелёный
    if percent < 40:
        return "#d97706"   # янтарный
    return "#dc2626"       # красный


def _highlight_spans(report: Report) -> list[tuple[int, int, str]]:
    """Непересекающиеся span'ы совпавших фрагментов: (start, end, url)."""
    spans = []
    for frag in report.matched_fragments:
        b = frag.best
        if b:
            spans.append((frag.shingle.start, frag.shingle.end, b.url))
    spans.sort()
    merged: list[tuple[int, int, str]] = []
    last_end = -1
    for start, end, url in spans:
        if start < last_end:      # пропускаем перекрытие
            continue
        merged.append((start, end, url))
        last_end = end
    return merged


def _render_body(report: Report) -> str:
    text = report.normalized_text
    spans = _highlight_spans(report)
    out: list[str] = []
    cursor = 0
    for start, end, url in spans:
        if start > cursor:
            out.append(html.escape(text[cursor:start]))
        frag = html.escape(text[start:end])
        safe_url = html.escape(url, quote=True)
        out.append(
            f'<a class="hit" href="{safe_url}" target="_blank" rel="noopener" '
            f'title="Источник: {safe_url}">{frag}</a>'
        )
        cursor = end
    if cursor < len(text):
        out.append(html.escape(text[cursor:]))
    return "".join(out)


def render_html(report: Report, *, title: str = "Отчёт о проверке на плагиат") -> str:
    p = report.percent
    color = _verdict_color(p)
    body = _render_body(report)

    rows = []
    for i, s in enumerate(report.top_sources(50), 1):
        url = html.escape(s.example_url, quote=True)
        rows.append(
            f"<tr><td>{i}</td><td>{html.escape(s.domain or '—')}</td>"
            f'<td><a href="{url}" target="_blank" rel="noopener">{url}</a></td>'
            f"<td class='num'>{s.fragments}</td><td class='num'>{s.max_score:.0f}%</td></tr>"
        )
    sources_table = "\n".join(rows) or '<tr><td colspan="5">Совпадений не найдено</td></tr>'

    warn = ""
    if report.stopped_early:
        warn = (
            f'<div class="warn">⚠ Проверка остановлена досрочно: '
            f'{html.escape(report.stop_reason)}. Осталось непроверенных фрагментов: '
            f'{report.remaining}. Запустите повторно — проверенное возьмётся из кэша.</div>'
        )

    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
  :root {{ color-scheme: light dark; }}
  * {{ box-sizing: border-box; }}
  body {{ font-family: -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif;
         margin: 0; background: #f6f7f9; color: #1a1a1a; line-height: 1.6; }}
  .wrap {{ max-width: 900px; margin: 0 auto; padding: 24px 16px 64px; }}
  header {{ text-align: center; padding: 28px 16px; }}
  .score {{ font-size: 64px; font-weight: 800; color: {color}; line-height: 1; }}
  .score small {{ font-size: 20px; font-weight: 600; color: #666; }}
  .sub {{ color: #555; margin-top: 8px; }}
  .meta {{ display: flex; flex-wrap: wrap; gap: 10px; justify-content: center; margin-top: 16px; }}
  .chip {{ background: #fff; border: 1px solid #e2e5e9; border-radius: 999px;
           padding: 6px 14px; font-size: 14px; box-shadow: 0 1px 2px rgba(0,0,0,.04); }}
  .chip b {{ color: #111; }}
  .warn {{ background: #fff7ed; border: 1px solid #fed7aa; color: #9a3412;
           padding: 12px 16px; border-radius: 10px; margin: 16px 0; }}
  h2 {{ font-size: 18px; margin: 32px 0 12px; }}
  .doc {{ background: #fff; border: 1px solid #e2e5e9; border-radius: 12px;
          padding: 24px; white-space: pre-wrap; word-wrap: break-word; }}
  a.hit {{ background: {color}22; border-bottom: 2px solid {color};
           color: inherit; text-decoration: none; padding: 0 1px; border-radius: 2px; }}
  a.hit:hover {{ background: {color}44; }}
  table {{ width: 100%; border-collapse: collapse; background: #fff;
           border: 1px solid #e2e5e9; border-radius: 12px; overflow: hidden; }}
  th, td {{ text-align: left; padding: 10px 12px; border-bottom: 1px solid #eef0f2;
            font-size: 14px; vertical-align: top; }}
  th {{ background: #fafbfc; font-weight: 600; }}
  td.num {{ text-align: right; white-space: nowrap; }}
  td a {{ color: #2563eb; word-break: break-all; }}
  footer {{ text-align: center; color: #9aa0a6; font-size: 12px; margin-top: 32px; }}
  @media (prefers-color-scheme: dark) {{
    body {{ background: #0f1115; color: #e6e6e6; }}
    .chip, .doc, table {{ background: #171a21; border-color: #2a2f3a; }}
    th {{ background: #1c2029; }}
    .sub, .chip {{ color: #b8bdc7; }}
    .chip b {{ color: #f2f4f8; }}
    th, td {{ border-color: #262b34; }}
    td a {{ color: #6ea8fe; }}
  }}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div class="score">{p:.1f}%<small> заимствовано</small></div>
    <div class="sub">Оригинальность: <b>{report.originality:.1f}%</b></div>
    <div class="meta">
      <span class="chip">Проверено фрагментов: <b>{len(report.checked_fragments)}</b> / {report.total_fragments}</span>
      <span class="chip">Совпало: <b>{len(report.matched_fragments)}</b></span>
      <span class="chip">Провайдер: <b>{html.escape(report.provider)}</b></span>
      <span class="chip">Порог: <b>{report.threshold:.0f}%</b></span>
      <span class="chip">Запросов: <b>{report.queries_used}</b> (+{report.cache_hits} из кэша)</span>
    </div>
  </header>
  {warn}
  <h2>Текст с подсветкой заимствований</h2>
  <div class="doc">{body}</div>
  <h2>Источники заимствований</h2>
  <table>
    <thead><tr><th>#</th><th>Домен</th><th>URL</th><th>Фрагментов</th><th>Макс. схожесть</th></tr></thead>
    <tbody>
      {sources_table}
    </tbody>
  </table>
  <footer>Сгенерировано {generated} · метод: точное совпадение фраз через поисковый API</footer>
</div>
</body>
</html>"""
