"""Формирование отчётов: консоль и самодостаточный HTML.

HTML-отчёт оформлен как «справка о результатах проверки текстового документа на
уникальность» — по вёрстке образца (шапка, круг с процентом уникальности, блок
статистики со списком источников, проверенный текст с подсветкой). Свёрстан под
печать A4 и просмотр в браузере. Брендинг — наш сервис; проверка выполняется
нашим движком (см. параметры brand/brand_note).
"""

from __future__ import annotations

import html
import re
from datetime import datetime, timezone

from .engine import Report

DEFAULT_BRAND = "Антиплагиат"
DEFAULT_BRAND_URL = ""   # домен в справке не показываем


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


# --- HTML-справка (по вёрстке образца, наш бренд) ---------------------------

def _uniq_color(uniqueness: float) -> str:
    """Цвет по уникальности: <50 красный, <85 оранжевый, иначе зелёный."""
    if uniqueness < 50:
        return "#ff0000"
    if uniqueness < 85:
        return "#ff9900"
    return "#4CAF50"


def render_html(
    report: Report,
    *,
    title: str = "Отчёт о проверке текста на уникальность",
    brand: str = DEFAULT_BRAND,
    brand_url: str = DEFAULT_BRAND_URL,
) -> str:
    uniqueness = report.originality
    color = _uniq_color(uniqueness)
    body = _render_body(report)
    text = report.normalized_text
    word_count = len(re.findall(r"\S+", text))
    generated = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")

    items = []
    for s in report.top_sources(50):
        url = html.escape(s.example_url, quote=True)
        items.append(f'<li><a href="{url}">{url}</a> ({s.max_score:.0f}% совпадения)</li>')
    sources_html = ("<ul>" + "".join(items) + "</ul>") if items else "Совпадений не найдено"

    brand_disp = html.escape(brand)
    brand_url_disp = html.escape(brand_url)
    url_sub = f" · {brand_url_disp}" if brand_url else ""
    url_foot = f" ({brand_url_disp})" if brand_url else ""

    warn = ""
    if report.stopped_early:
        warn = (
            f'<div class="warn">Проверка остановлена досрочно: '
            f'{html.escape(report.stop_reason)}. Непроверенных фрагментов: {report.remaining}.</div>'
        )

    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
  @page {{ size: A4 portrait; margin: 20mm; @bottom-right {{ content: counter(page);
           font-size: 12px; color: #666; }} }}
  * {{ box-sizing: border-box; }}
  body {{ font-family: 'Times New Roman', 'DejaVu Serif', serif; margin: 0;
         background: #eceff1; color: #000; }}
  .sheet {{ max-width: 820px; margin: 24px auto; background: #fff; padding: 38px 46px 52px;
            box-shadow: 0 2px 16px rgba(0,0,0,.12); }}
  .header {{ text-align: center; margin-bottom: 12px; padding-bottom: 8px;
             border-bottom: 1px solid #e0e0e0; }}
  .title {{ font-size: 22px; font-weight: bold; color: #1a3c5e; margin: 0; line-height: 1.25; }}
  .subtitle {{ font-size: 14px; color: #666; margin-top: 6px; }}
  .stats-container {{ display: flex; justify-content: space-between; align-items: flex-start;
                      gap: 20px; }}
  .stats-left {{ flex-grow: 1; min-width: 0; }}
  .stat-item {{ font-size: 14px; margin-bottom: 6px; }}
  .stat-item .label {{ color: #008000; font-weight: bold; }}
  .stat-item .value {{ color: #000; word-break: break-word; }}
  .uniqueness-value {{ color: {color}; font-weight: bold; }}
  ul {{ padding-left: 18px; margin: 4px 0 0; }}
  li {{ font-size: 12.5px; margin-bottom: 4px; }}
  a {{ color: #1a0dab; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  .uniqueness-circle {{ flex-shrink: 0; width: 96px; height: 96px; border-radius: 50%;
             display: flex; align-items: center; justify-content: center; white-space: nowrap;
             font-size: 17px; font-weight: bold; border: 8px solid {color}; color: {color}; }}
  .warn {{ background: #fff7ed; border: 1px solid #fed7aa; color: #9a3412;
           padding: 10px 14px; border-radius: 8px; margin: 12px 0; font-size: 13px; }}
  .section-title {{ font-size: 16px; font-weight: bold; color: #1a3c5e; margin: 20px 0 6px;
                    padding-bottom: 3px; border-bottom: 1px dashed #e0e0e0; }}
  .text-content {{ font-size: 14px; line-height: 1.35; text-align: justify; hyphens: auto;
                   white-space: pre-wrap; word-wrap: break-word; }}
  a.hit {{ background: #ffe2e2; border-bottom: 1px solid {color}; color: #000;
           text-decoration: none; padding: 0 1px; }}
  a.hit:hover {{ background: #ffcccc; }}
  footer {{ margin-top: 22px; padding-top: 8px; border-top: 1px solid #e0e0e0;
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
    <div class="title">о результатах проверки текстового документа на уникальность</div>
    <div class="subtitle">Проверка выполнена в сервисе «{brand_disp}»{url_sub}</div>
  </div>

  {warn}

  <div class="stats-container">
    <div class="stats-left">
      <div class="stat-item"><span class="label">Дата и время проверки:</span> <span class="value">{generated}</span></div>
      <div class="stat-item"><span class="label">Процент уникальности:</span> <span class="value uniqueness-value">{uniqueness:.2f}%</span></div>
      <div class="stat-item"><span class="label">Количество слов:</span> <span class="value">{word_count}</span></div>
      <div class="stat-item"><span class="label">Источники совпадений:</span> <span class="value">{sources_html}</span></div>
    </div>
    <div class="uniqueness-circle">{uniqueness:.0f}%</div>
  </div>

  <div class="section-title">Проверенный текст</div>
  <div class="text-content">{body}</div>

  <footer>
    Проверено {generated} · сервис «{brand_disp}»{url_foot}. Метод: поиск дословных
    совпадений фраз в открытом вебе. Отчёт носит справочный характер.
  </footer>
</div>
</body>
</html>"""
