"""Work that takes longer than a web request should: one job at a time, in the background.

A forecast of thousands of series takes minutes. The web server answers at
once with a job, and the front end asks for the job's progress until it is
done. Jobs run one after the other, because the model answers one request at
a time.
"""

from __future__ import annotations

import datetime as dt
import logging
import queue
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

QUEUED = "queued"
RUNNING = "running"
DONE = "done"
FAILED = "failed"


@dataclass
class Job:
    """One piece of background work and how far it has got."""

    id: str
    label: str
    state: str = QUEUED
    stage: str = "Waiting to start"
    progress: float = 0.0
    """From 0 to 1."""
    error: str | None = None
    result: dict[str, Any] | None = None
    created_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.UTC))
    finished_at: dt.datetime | None = None

    def report(self, stage: str, progress: float) -> None:
        """Say what the job is doing and how far along it is."""
        self.stage = stage
        self.progress = max(0.0, min(1.0, progress))

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "state": self.state,
            "stage": self.stage,
            "progress": round(self.progress, 3),
            "error": self.error,
            "result": self.result,
            "seconds": round(
                ((self.finished_at or dt.datetime.now(dt.UTC)) - self.created_at).total_seconds(), 1
            ),
        }


class JobRunner:
    """Runs jobs one at a time on a background thread and remembers the recent ones."""

    def __init__(self, keep: int = 50) -> None:
        self._jobs: dict[str, Job] = {}
        self._work: queue.Queue[tuple[Job, Callable[[Job], dict[str, Any]]]] = queue.Queue()
        self._keep = keep
        self._thread = threading.Thread(target=self._run, name="u4cast-jobs", daemon=True)
        self._thread.start()

    def submit(self, label: str, work: Callable[[Job], dict[str, Any]]) -> Job:
        """Queue `work`. It is called with its job and returns the job's result."""
        job = Job(id=uuid.uuid4().hex, label=label)
        self._jobs[job.id] = job
        for old in list(self._jobs)[: -self._keep]:
            if self._jobs[old].state in (DONE, FAILED):
                del self._jobs[old]
        self._work.put((job, work))
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def active(self) -> list[Job]:
        """Return the jobs that are waiting or running, oldest first."""
        return [job for job in self._jobs.values() if job.state in (QUEUED, RUNNING)]

    def _run(self) -> None:
        while True:
            job, work = self._work.get()
            job.state = RUNNING
            try:
                job.result = work(job)
                job.report("Done", 1.0)
                job.state = DONE
            except Exception as error:
                logger.exception("Job %s failed: %s", job.id, job.label)
                job.error = str(error) or type(error).__name__
                job.state = FAILED
            finally:
                job.finished_at = dt.datetime.now(dt.UTC)
