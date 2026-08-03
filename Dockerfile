FROM python:3.12-slim

WORKDIR /app

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

CMD ["gunicorn", "--bind", "0.0.0.0:8790", "--workers", "2", "--timeout", "120", "webapp.app:app"]
