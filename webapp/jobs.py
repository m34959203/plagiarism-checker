"""Фоновая обработка проверок для веба.

Синхронный HTTP-запрос ограничен ~100 c (лимит Cloudflare), поэтому большой
текст (до 15 000 слов ≈ тысячи фраз-запросов) нельзя проверить за один запрос.
Здесь задача ставится в очередь и крутится в фоновом потоке через весь пул
ключей serper; страница опрашивает статус и показывает прогресс, а по готовности
— полный HTML-отчёт.

Хранилище задач — в памяти процесса, поэтому веб-приложение должно запускаться
одним воркером с потоками (gunicorn --workers 1 --threads N).
"""

from __future__ import annotations

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Optional

from plagiarism.cache import Cache
from plagiarism.engine import check_text
from plagiarism.report import render_html


@dataclass
class Job:
    id: str
    status: str = "queued"          # queued | running | done | error
    total: int = 0                  # всего фрагментов (известно после старта)
    checked: int = 0                # проверено фрагментов
    queries_used: int = 0
    percent: float = 0.0
    stopped_early: bool = False
    stop_reason: str = ""
    error: str = ""
    report_html: str = ""
    created: float = 0.0
    finished: float = 0.0

    def public(self) -> dict:
        """Сериализуемый статус для JSON (без тяжёлого report_html)."""
        pct = int(self.checked / self.total * 100) if self.total else 0
        return {
            "id": self.id,
            "status": self.status,
            "total": self.total,
            "checked": self.checked,
            "progress": pct,
            "queries_used": self.queries_used,
            "percent": self.percent,
            "stopped_early": self.stopped_early,
            "stop_reason": self.stop_reason,
            "error": self.error,
        }


class JobManager:
    def __init__(
        self,
        provider_factory: Callable[[], object],
        *,
        cache_path: str,
        threshold: float = 80.0,
        delay: float = 0.2,
        max_queries: Optional[int] = None,
        workers: int = 2,
        keep: int = 60,
    ):
        self._provider_factory = provider_factory
        self._cache_path = cache_path
        self._threshold_default = threshold
        self._delay = delay
        self._max_queries = max_queries
        self._keep = keep
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="plagjob")

    # --- API ---------------------------------------------------------------
    def submit(self, text: str, threshold: Optional[float] = None) -> str:
        job = Job(id=uuid.uuid4().hex[:12], created=time.time())
        with self._lock:
            self._jobs[job.id] = job
            self._prune_locked()
        self._pool.submit(self._run, job, text, threshold or self._threshold_default)
        return job.id

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    # --- внутреннее --------------------------------------------------------
    def _prune_locked(self) -> None:
        if len(self._jobs) <= self._keep:
            return
        # удаляем самые старые завершённые
        done = sorted(
            (j for j in self._jobs.values() if j.status in ("done", "error")),
            key=lambda j: j.finished or j.created,
        )
        for j in done[: len(self._jobs) - self._keep]:
            self._jobs.pop(j.id, None)

    def _run(self, job: Job, text: str, threshold: float) -> None:
        job.status = "running"
        try:
            provider = self._provider_factory()
            cache = Cache(self._cache_path)

            def cb(p) -> None:
                job.checked = p.index
                job.total = p.total
                job.queries_used = p.queries_used

            report = check_text(
                text, provider, cache=cache, threshold=threshold,
                max_queries=self._max_queries, delay=self._delay, progress_cb=cb,
            )
            job.percent = report.percent
            job.stopped_early = report.stopped_early
            job.stop_reason = report.stop_reason
            job.report_html = render_html(report, title="Отчёт · Антиплагиат")
            job.status = "done"
        except Exception as exc:  # фон не должен падать молча
            job.status = "error"
            job.error = str(exc)
        finally:
            job.finished = time.time()
