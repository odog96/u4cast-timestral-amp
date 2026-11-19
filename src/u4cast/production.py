"""Forecasts in production: issued again on a schedule, by a Cloudera AI job.

A forecast that someone likes is put in production. From then on a job
issues it again on a schedule, each time from the history as it stands, and
each run is appended to the same forecast values table as a new issue. This
module holds what is stored about that, and the scheduler that creates the
job. The run itself is in `production/run_forecast.py`.

Two tables, both in the application's database:

- `schedules`: one row per forecast in production.
- `production_runs`: one row per run of a schedule, with what it did.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any, Protocol

logger = logging.getLogger(__name__)

ACTIVE = "active"
PAUSED = "paused"
RETIRED = "retired"

ISSUED = "issued"
"""A run made a new issue."""
NO_NEW_DATA = "no_new_data"
"""A run found no history newer than the last issue, and made nothing."""
FAILED = "failed"

SCHEDULES_TABLE: tuple[tuple[str, str], ...] = (
    ("dataset", "string"),
    ("forecast_name", "string"),
    ("cron", "string"),
    ("state", "string"),
    ("job_id", "string"),
    ("job_name", "string"),
    ("created_at", "timestamp"),
    ("updated_at", "timestamp"),
)

RUNS_TABLE: tuple[tuple[str, str], ...] = (
    ("dataset", "string"),
    ("forecast_name", "string"),
    ("ran_at", "timestamp"),
    ("outcome", "string"),
    ("detail", "string"),
    ("forecast_id", "string"),
    ("history_end_date", "date"),
    ("series", "bigint"),
    ("seconds", "double"),
)

JOB_SCRIPT = "production/run_as_job.py"
DATASET_VARIABLE = "U4CAST_PRODUCTION_DATASET"
FORECAST_VARIABLE = "U4CAST_PRODUCTION_FORECAST"

_FIELD = re.compile(r"^[0-9*/,\-]+$")


def checked_cron(cron: str) -> str:
    """Return a schedule in cron form, five fields, or refuse it.

    The fields are minute, hour, day of the month, month and day of the
    week, for example `0 6 1 * *` for six o'clock on the first of the month.
    """
    fields = cron.split()
    if len(fields) != 5 or not all(_FIELD.match(field) for field in fields):
        raise ValueError(
            "A schedule has five fields: minute, hour, day of the month, month, "
            "day of the week. For example 0 6 1 * *."
        )
    return " ".join(fields)


def _utc(value: Any) -> dt.datetime:
    stamp = value.to_pydatetime() if hasattr(value, "to_pydatetime") else value
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=dt.UTC)


@dataclass(frozen=True)
class Schedule:
    """One forecast in production."""

    dataset: str
    forecast_name: str
    cron: str
    state: str
    job_id: str
    job_name: str
    created_at: dt.datetime
    updated_at: dt.datetime

    def to_row(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name, _ in SCHEDULES_TABLE}

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> Schedule:
        values = {name: row[name] for name, _ in SCHEDULES_TABLE}
        values["created_at"] = _utc(values["created_at"])
        values["updated_at"] = _utc(values["updated_at"])
        return cls(**values)

    def changed(self, state: str) -> Schedule:
        return replace(self, state=state, updated_at=dt.datetime.now(dt.UTC))


def job_name(dataset: str, forecast_name: str) -> str:
    return f"u4cast production: {forecast_name} ({dataset})"


class Scheduler(Protocol):
    """Creates and controls the job behind a schedule."""

    def create(self, name: str, cron: str, environment: Mapping[str, str]) -> str: ...

    def pause(self, job_id: str, paused: bool) -> None: ...

    def delete(self, job_id: str) -> None: ...

    def address(self, job_id: str) -> str | None: ...


class ClouderaScheduler:
    """Schedules with Cloudera AI jobs, one job per forecast in production.

    The job runs with the Spark and Hadoop add-ons, like the application,
    because it reads history from the data lake and appends the new issue.
    Schedules are in UTC.
    """

    def __init__(self, cpu: float = 2, memory: float = 8) -> None:
        self.cpu = cpu
        self.memory = memory

    def _client(self) -> tuple[Any, str]:
        import cmlapi

        return cmlapi.default_client(), os.environ["CDSW_PROJECT_ID"]

    def create(self, name: str, cron: str, environment: Mapping[str, str]) -> str:
        import cmlapi

        from webapp.deploy_application import addons, newest_runtime

        client, project_id = self._client()
        for job in client.list_jobs(project_id, page_size=500).jobs:
            if job.name == name:
                client.delete_job(project_id, job.id)
        job = client.create_job(
            cmlapi.CreateJobRequest(
                project_id=project_id,
                name=name,
                script=JOB_SCRIPT,
                kernel="python3",
                cpu=self.cpu,
                memory=self.memory,
                schedule=checked_cron(cron),
                timezone="UTC",
                environment=json.dumps(dict(environment)),
                runtime_identifier=newest_runtime(client),
                runtime_addon_identifiers=addons(client),
            ),
            project_id,
        )
        logger.info("Created the job %s on the schedule %s", name, cron)
        return job.id

    def pause(self, job_id: str, paused: bool) -> None:
        import cmlapi

        client, project_id = self._client()
        client.update_job(cmlapi.Job(paused=paused), project_id, job_id)

    def delete(self, job_id: str) -> None:
        client, project_id = self._client()
        client.delete_job(project_id, job_id)

    def address(self, job_id: str) -> str | None:
        project = os.environ.get("CDSW_PROJECT_URL")
        return f"{project}/jobs" if project else None
