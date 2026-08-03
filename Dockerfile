FROM python:3.12-slim

WORKDIR /app

# системные библиотеки для WeasyPrint (генерация PDF) + шрифты с кириллицей
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf-2.0-0 \
        libffi8 libcairo2 shared-mime-info fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# зависимости отдельно — лучше кэшируется
COPY requirements.txt requirements-web.txt ./
RUN pip install --no-cache-dir -r requirements-web.txt

COPY plagiarism/ ./plagiarism/
COPY webapp/ ./webapp/

ENV PORT=8790 \
    WEB_PROVIDER=serper \
    WEB_MAX_QUERIES=30 \
    WEB_CACHE=/data/web_cache.json

VOLUME ["/data"]
EXPOSE 8790

# один воркер + потоки: фоновые задачи хранятся в памяти процесса,
# поэтому опрос статуса должен попадать в тот же воркер
CMD ["gunicorn", "--bind", "0.0.0.0:8790", "--workers", "1", "--threads", "8", "--timeout", "120", "webapp.app:app"]
