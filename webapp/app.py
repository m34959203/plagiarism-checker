"""Публичное веб-демо проверки на плагиат.

Тонкая обёртка над ``plagiarism.engine``: пользователь вставляет текст (или
загружает .txt/.md), приложение прогоняет проверку выбранным провайдером и
показывает тот же HTML-отчёт, что и CLI. Для защиты бесплатного лимита API
число запросов на один прогон ограничено ``WEB_MAX_QUERIES``, а результаты
кэшируются на диск и переиспользуются между запросами.
"""

from __future__ import annotations

import os
import re

from flask import Flask, render_template_string, request

from plagiarism.cache import Cache
from plagiarism.engine import check_text
from plagiarism.providers import ProviderError, get_provider
from plagiarism.report import render_html

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

app = Flask(__name__)
# формат числа с неразрывными пробелами: 15000 -> "15 000"
app.jinja_env.filters["sp"] = lambda n: f"{int(n):,}".replace(",", " ")

WEB_PROVIDER = os.environ.get("WEB_PROVIDER", "serper")
WEB_MAX_QUERIES = int(os.environ.get("WEB_MAX_QUERIES", "100"))
WEB_THRESHOLD = float(os.environ.get("WEB_THRESHOLD", "80"))
WEB_MAX_WORDS = int(os.environ.get("WEB_MAX_WORDS", "15000"))
MIN_CHARS = int(os.environ.get("WEB_MIN_CHARS", "200"))
# страховочный потолок символов (запас под 15k слов); при превышении — обрезаем
MAX_CHARS = int(os.environ.get("WEB_MAX_CHARS", str(WEB_MAX_WORDS * 8)))
CACHE_PATH = os.environ.get("WEB_CACHE", os.path.join(os.path.dirname(__file__), "web_cache.json"))


def _count_words(text: str) -> int:
    return len(re.findall(r"\S+", text))


def _serper_key_count() -> int:
    """Сколько ключей в пуле provider'а (для показа общего запаса на странице)."""
    try:
        p = get_provider(WEB_PROVIDER)
        return getattr(p, "key_count", 1)
    except Exception:
        return 0


KEYS_COUNT = _serper_key_count()
TOTAL_QUOTA = KEYS_COUNT * 2500  # общий бесплатный запас запросов по всем ключам

INDEX = r"""<!DOCTYPE html>
<html lang="ru"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Антиплагиат · проверка текста по интернету</title>
<style>
  :root { color-scheme: light dark; }
  * { box-sizing: border-box; }
  body { font-family: -apple-system, Segoe UI, Roboto, Arial, sans-serif; margin: 0;
         background: #f6f7f9; color: #1a1a1a; line-height: 1.55; }
  .wrap { max-width: 760px; margin: 0 auto; padding: 40px 16px 64px; }
  h1 { font-size: 30px; margin: 0 0 6px; }
  p.lead { color: #555; margin: 0 0 24px; }
  form { background: #fff; border: 1px solid #e2e5e9; border-radius: 14px; padding: 22px;
         box-shadow: 0 1px 3px rgba(0,0,0,.05); }
  textarea { width: 100%; min-height: 240px; border: 1px solid #d7dbe0; border-radius: 10px;
             padding: 14px; font: inherit; resize: vertical; }
  .row { display: flex; flex-wrap: wrap; gap: 14px; align-items: center; margin-top: 14px; }
  label { font-size: 14px; color: #444; }
  input[type=number] { width: 80px; padding: 6px 8px; border: 1px solid #d7dbe0; border-radius: 8px; }
  input[type=file] { font-size: 14px; }
  button { background: #2563eb; color: #fff; border: 0; border-radius: 10px;
           padding: 12px 22px; font-size: 16px; font-weight: 600; cursor: pointer; margin-left: auto; }
  button:hover { background: #1d4ed8; }
  .note { color: #888; font-size: 13px; margin-top: 18px; }
  .counter { display: flex; justify-content: space-between; gap: 10px; flex-wrap: wrap;
             font-size: 13px; color: #888; margin-top: 6px; }
  .counter b { color: #444; font-variant-numeric: tabular-nums; }
  .counter.over b#cc { color: #dc2626; }
  .limits { background: #eff6ff; border: 1px solid #dbeafe; color: #1e40af;
            border-radius: 10px; padding: 10px 14px; font-size: 13px; margin: 14px 0 0; }
  .limits b { font-weight: 600; }
  .err { background: #fef2f2; border: 1px solid #fecaca; color: #b91c1c;
         padding: 12px 16px; border-radius: 10px; margin-bottom: 18px; }
  a { color: #2563eb; }
  @media (prefers-color-scheme: dark) {
    body { background: #0f1115; color: #e6e6e6; }
    form { background: #171a21; border-color: #2a2f3a; }
    textarea, input[type=number] { background: #0f1115; color: #e6e6e6; border-color: #2a2f3a; }
    p.lead, label, .note, .counter { color: #b8bdc7; }
    .counter b { color: #e6e6e6; }
    .limits { background: #172033; border-color: #24324d; color: #93c5fd; }
  }
</style></head>
<body><div class="wrap">
  <h1>🔍 Антиплагиат</h1>
  <p class="lead">Проверка текста на дословные заимствования по всему интернету.
     Вставьте текст или загрузите файл — получите процент заимствований, подсветку
     совпадений и ссылки на источники.</p>
  {% if error %}<div class="err">{{ error }}</div>{% endif %}
  <form method="post" action="/check" enctype="multipart/form-data">
    <textarea id="ta" name="text" maxlength="{{ max_chars }}"
              placeholder="Вставьте сюда текст для проверки…">{{ text or '' }}</textarea>
    <div class="counter" id="counter">
      <span><b id="cw">0</b> / {{ max_words|sp }} слов · <b id="cc">0</b> символов</span>
      <span>минимум {{ min_chars }} символов</span>
    </div>
    <div class="limits">
      📏 Максимум <b>{{ max_words|sp }} слов</b> за проверку. За один прогон проверяется
      до <b>{{ max_queries }}</b> фрагментов-фраз; для полного покрытия длинного текста
      запускайте повторно — проверенное берётся из кэша и не тратит лимит.
      {% if keys_count > 1 %}<br>🔑 Пул из <b>{{ keys_count }}</b> ключей serper — общий запас ≈ <b>{{ total_quota|sp }}</b> запросов, переключение при исчерпании автоматическое.{% endif %}
    </div>
    <div class="row">
      <label>или файл: <input type="file" name="file" accept=".txt,.md"></label>
      <label>порог схожести, %: <input type="number" name="threshold" value="{{ threshold }}" min="50" max="100"></label>
      <button type="submit">Проверить</button>
    </div>
    <div class="note">
      Провайдер: <b>{{ provider }}</b>. Метод ловит прежде всего дословные заимствования;
      перефразирование — хуже.
    </div>
  </form>
  <script>
    (function () {
      var ta = document.getElementById('ta');
      var cc = document.getElementById('cc'), cw = document.getElementById('cw');
      var counter = document.getElementById('counter');
      var MAXC = {{ max_chars }}, MAXW = {{ max_words }}, MIN = {{ min_chars }};
      var nf = new Intl.NumberFormat('ru-RU');
      function upd() {
        var t = ta.value;
        var chars = t.length;
        var words = (t.trim().match(/\S+/g) || []).length;
        cc.textContent = nf.format(chars);
        cw.textContent = nf.format(words);
        counter.classList.toggle('over', words > MAXW || chars >= MAXC || (chars > 0 && chars < MIN));
      }
      ta.addEventListener('input', upd);
      upd();
    })();
  </script>
  <p class="note">CLI-версия и исходный код: <a href="https://github.com/m34959203/plagiarism-checker" target="_blank" rel="noopener">github.com/m34959203/plagiarism-checker</a></p>
</div></body></html>"""

BACK_BANNER = """<div style="position:sticky;top:0;background:#111;color:#fff;padding:10px 16px;
font:14px -apple-system,Segoe UI,Arial,sans-serif;text-align:center;z-index:99">
<a href="/" style="color:#8ab4ff;text-decoration:none">← проверить другой текст</a></div>"""


@app.get("/")
def index():
    return render_template_string(
        INDEX, provider=WEB_PROVIDER, max_queries=WEB_MAX_QUERIES,
        threshold=int(WEB_THRESHOLD), text=None, error=None,
        max_chars=MAX_CHARS, min_chars=MIN_CHARS, max_words=WEB_MAX_WORDS,
        keys_count=KEYS_COUNT, total_quota=TOTAL_QUOTA,
    )


@app.get("/health")
def health():
    return {"status": "ok", "provider": WEB_PROVIDER}


@app.post("/check")
def check():
    text = (request.form.get("text") or "").strip()
    upload = request.files.get("file")
    if upload and upload.filename:
        try:
            text = upload.read().decode("utf-8", errors="replace").strip()
        except Exception:
            text = text
    try:
        threshold = float(request.form.get("threshold") or WEB_THRESHOLD)
    except ValueError:
        threshold = WEB_THRESHOLD

    if len(text) < MIN_CHARS:
        return _index_error(
            f"Слишком короткий текст — минимум {MIN_CHARS} символов.", text, threshold
        )
    # ограничение по словам (основной лимит) + страховочный потолок по символам
    if _count_words(text) > WEB_MAX_WORDS:
        text = " ".join(re.findall(r"\S+", text)[:WEB_MAX_WORDS])
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS]

    try:
        provider = get_provider(WEB_PROVIDER)
    except ProviderError as exc:
        return _index_error(f"Провайдер не настроен: {exc}", text, threshold)

    cache = Cache(CACHE_PATH)
    try:
        report = check_text(
            text, provider, cache=cache, threshold=threshold,
            max_queries=WEB_MAX_QUERIES, delay=0.0,
        )
    except Exception as exc:  # не роняем демо на непредвиденной ошибке
        return _index_error(f"Ошибка при проверке: {exc}", text, threshold)

    return BACK_BANNER + render_html(report, title="Отчёт · Антиплагиат")


def _index_error(msg: str, text: str, threshold: float):
    return render_template_string(
        INDEX, provider=WEB_PROVIDER, max_queries=WEB_MAX_QUERIES,
        threshold=int(threshold), text=text, error=msg,
        max_chars=MAX_CHARS, min_chars=MIN_CHARS, max_words=WEB_MAX_WORDS,
        keys_count=KEYS_COUNT, total_quota=TOTAL_QUOTA,
    ), 400


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8790")))
