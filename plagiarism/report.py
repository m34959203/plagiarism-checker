"""Формирование отчётов: консоль и самодостаточный HTML.

HTML-отчёт оформлен как «справка о результатах проверки текста на заимствования»
(вдохновлён шаблоном antiplagiat_service): формальная шапка, круг с процентом
уникальности, блок статистики со списком источников и проверенный текст с
подсветкой совпадений. Свёрстан под печать A4 и просмотр в браузере.
"""

from __future__ import annotations

import hashlib
import html
import re
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
        lines.append("  Запустите проверку повторно — проверенные фразы возьмутся из кэша.")
    lines.append("=" * 56)
    return "\n".join(lines)


# --- подсветка совпадений в тексте ------------------------------------------

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


# --- HTML-справка -----------------------------------------------------------

def _circle_color(originality: float) -> str:
    """Цвет круга уникальности: <50 красный, <85 оранжевый, иначе зелёный."""
    if originality < 50:
        return "#e53935"
    if originality < 85:
        return "#fb8c00"
    return "#43a047"


def render_html(report: Report, *, title: str = "Справка о проверке текста") -> str:
    orig = report.originality
    color = _circle_color(orig)
    body = _render_body(report)

    text = report.normalized_text
    word_count = len(re.findall(r"\S+", text))
    char_count = len(text)
    char_count_ns = len(re.sub(r"\s+", "", text))
    generated = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
    report_id = hashlib.sha1((text + generated).encode("utf-8")).hexdigest()[:10].upper()

    # список источников совпадений
    items = []
    for s in report.top_sources(50):
        url = html.escape(s.example_url, quote=True)
        items.append(
            f'<li><a href="{url}" target="_blank" rel="noopener">{url}</a> '
            f'— {s.max_score:.0f}% совпадения, фрагментов: {s.fragments}</li>'
        )
    sources_html = ("<ul>" + "".join(items) + "</ul>") if items else \
        '<span class="muted">Совпадений не найдено</span>'

    warn = ""
    if report.stopped_early:
        warn = (
            f'<div class="warn">⚠ Проверка остановлена досрочно: '
            f'{html.escape(report.stop_reason)}. Непроверенных фрагментов: '
            f'{report.remaining}. При повторном запуске проверенное берётся из кэша.</div>'
        )

    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
  @page {{ size: A4 portrait; margin: 18mm; }}
  * {{ box-sizing: border-box; }}
  body {{ font-family: 'Times New Roman', Georgia, serif; margin: 0;
         background: #eceff1; color: #000; }}
  .sheet {{ max-width: 820px; margin: 24px auto; background: #fff; padding: 40px 48px 56px;
            box-shadow: 0 2px 16px rgba(0,0,0,.12); }}
  .header {{ text-align: center; margin-bottom: 18px; padding-bottom: 12px;
             border-bottom: 1px solid #e0e0e0; }}
  .title {{ font-size: 22px; font-weight: bold; color: #1a3c5e; margin: 0; line-height: 1.25; }}
  .subtitle {{ font-size: 14px; color: #666; margin-top: 6px; }}
  .stats {{ display: flex; justify-content: space-between; align-items: flex-start; gap: 24px; }}
  .stats-left {{ flex-grow: 1; min-width: 0; }}
  .stat {{ font-size: 14px; margin-bottom: 7px; }}
  .stat .label {{ color: #008000; font-weight: bold; }}
  .stat .value {{ color: #000; word-break: break-word; }}
  .muted {{ color: #777; }}
  ul {{ padding-left: 18px; margin: 4px 0 0; }}
  li {{ font-size: 12.5px; margin-bottom: 4px; }}
  a {{ color: #1a0dab; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  .circle-wrap {{ flex-shrink: 0; display: flex; flex-direction: column; align-items: center; }}
  .circle {{ width: 118px; height: 118px; border-radius: 50%;
             display: flex; align-items: center; justify-content: center;
             border: 9px solid {color}; color: {color}; }}
  .circle .num {{ font-size: 28px; font-weight: bold; line-height: 1; }}
  .circle-cap {{ font-size: 11px; color: #666; margin-top: 7px; text-transform: uppercase;
                 letter-spacing: .5px; }}
  .warn {{ background: #fff7ed; border: 1px solid #fed7aa; color: #9a3412;
           padding: 10px 14px; border-radius: 8px; margin: 14px 0; font-size: 13px; }}
  .section-title {{ font-size: 16px; font-weight: bold; color: #1a3c5e; margin: 22px 0 8px;
                    padding-bottom: 3px; border-bottom: 1px dashed #e0e0e0; }}
  .text-content {{ font-size: 14px; line-height: 1.45; text-align: justify; hyphens: auto;
                   white-space: pre-wrap; word-wrap: break-word; }}
  a.hit {{ background: #ffe2e2; border-bottom: 1px solid {color}; color: #000;
           text-decoration: none; padding: 0 1px; }}
  a.hit:hover {{ background: #ffcccc; }}
  footer {{ margin-top: 26px; padding-top: 10px; border-top: 1px solid #e0e0e0;
            font-size: 11px; color: #888; text-align: center; }}
  @media print {{
    body {{ background: #fff; }}
    .sheet {{ box-shadow: none; margin: 0; max-width: none; padding: 0; }}
    a {{ color: #000; }}
  }}
</style>
</head>
<body>
<div class="sheet">
  <div class="header">
    <div class="title">СПРАВКА</div>
    <div class="title">о результатах проверки текста на заимствования</div>
    <div class="subtitle">Проверка выполнена в сервисе «Антиплагиат» · antiplagiat.technokod.kz</div>
  </div>

  {warn}

  <div class="stats">
    <div class="stats-left">
      <div class="stat"><span class="label">Дата и время проверки:</span> <span class="value">{generated}</span></div>
      <div class="stat"><span class="label">Идентификатор отчёта:</span> <span class="value">{report_id}</span></div>
      <div class="stat"><span class="label">Процент оригинальности:</span> <span class="value">{orig:.1f}%</span></div>
      <div class="stat"><span class="label">Процент заимствований:</span> <span class="value">{report.percent:.1f}%</span></div>
      <div class="stat"><span class="label">Количество слов:</span> <span class="value">{word_count}</span></div>
      <div class="stat"><span class="label">Количество символов:</span> <span class="value">{char_count} (без пробелов: {char_count_ns})</span></div>
      <div class="stat"><span class="label">Проверено фрагментов:</span> <span class="value">{len(report.checked_fragments)} из {report.total_fragments}, совпало: {len(report.matched_fragments)}</span></div>
      <div class="stat"><span class="label">Порог совпадения:</span> <span class="value">{report.threshold:.0f}% · провайдер: {html.escape(report.provider)}</span></div>
      <div class="stat"><span class="label">Источники совпадений:</span> <span class="value">{sources_html}</span></div>
    </div>
    <div class="circle-wrap">
      <div class="circle"><span class="num">{orig:.0f}%</span></div>
      <div class="circle-cap">оригинальность</div>
    </div>
  </div>

  <div class="section-title">Проверенный текст</div>
  <div class="text-content">{body}</div>

  <footer>
    Метод: точное совпадение фраз через поисковый API. Отчёт носит справочный характер
    и отражает дословные заимствования, найденные в открытом вебе. · {generated}
  </footer>
</div>
</body>
</html>"""
