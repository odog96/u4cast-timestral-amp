"""What the web server does with data.

One `Workspace` holds the connection to the data lake, the model, and what is
cached in memory. Every screen of the front end is answered by one of its
methods. Spark is asked as little as possible: the lists of datasets and
forecasts are read once, and a slice or a forecast's values are read once and
then kept, so a chart does not wait for a query.
"""

from __future__ import annotations

import datetime as dt
import io
import json
import logging
import math
import re
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from u4cast.backtest import Scores, score_forecasts
from u4cast.endpoint import BLOCK_STEPS
from u4cast.forecast import IssuedForecast, issue_forecast, reissue_forecast
from u4cast.frequency import SEASON_LENGTH, Frequency, detect_frequency, minimum_history
from u4cast.layout import (
    DatasetRecord,
    ForecastRecord,
    Kind,
    forecast_values_columns,
    forecast_values_table,
)
from u4cast.model import Forecaster
from u4cast.prepare import TS_ID, Fill, Panel, prepare
from webapp.jobs import Job

logger = logging.getLogger(__name__)

# A slice is refused above these sizes, before it is loaded.
MAX_SLICE_ROWS = 5_000_000
MAX_SERIES = 25_000
DATES_FRESH_SECONDS = 60
"""How long the dates of a history table are trusted before it is asked again for new rows."""
MAX_UPLOAD_BYTES = 200 * 1024 * 1024
# Measured on the workbench model with 4 vCPU: see docs/decisions.md, check 6.
SECONDS_PER_SERIES_BLOCK = 0.0027
SECONDS_PER_REQUEST = 0.4
# The most issues one comparison shows. More do not fit one chart.
MAX_COMPARED = 5
NUMBER_TYPES = ("double", "float", "decimal", "int", "bigint", "smallint", "tinyint", "long")
TARGET_HINTS = ("sales", "units", "qty", "quantity", "bottles", "amount", "volume", "revenue")


class UserError(ValueError):
    """Something the user asked for cannot be done. The message is shown as it is."""


class NotReadyError(RuntimeError):
    """The connection to the data lake is not up yet, or failed."""


def iso(value: Any) -> str | None:
    """Write a date as text, for example `2026-10-01`."""
    if value is None or (isinstance(value, float) and math.isnan(value)) or value is pd.NaT:
        return None
    return pd.Timestamp(value).strftime("%Y-%m-%d")


def number(value: Any) -> float | None:
    """Write a number for JSON: None where it is missing."""
    if value is None:
        return None
    value = float(value)
    return None if math.isnan(value) or math.isinf(value) else value


def numbers(values: Any) -> list[float | None]:
    return [number(value) for value in np.asarray(values, dtype=np.float64).ravel()]


def plain(value: Any) -> Any:
    """Turn one cell of a data frame into something JSON can carry."""
    if value is None or value is pd.NaT:
        return None
    if isinstance(value, float | np.floating):
        return number(value)
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, pd.Timestamp | dt.date | dt.datetime):
        return iso(value) if pd.Timestamp(value) == pd.Timestamp(value).normalize() else str(value)
    if isinstance(value, str | int | bool):
        return value
    return str(value)


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


@dataclass(frozen=True)
class SliceSpec:
    """Which series of a dataset, and which stretch of their history."""

    dataset: str
    dimension_columns: tuple[str, ...]
    slice_filter: Mapping[str, Sequence[Any]] = field(default_factory=dict)
    history_start: str | None = None
    history_end: str | None = None
    minimum_history: int | None = None
    target_column: str | None = None
    """The number to forecast. None means the one chosen when the dataset was added."""

    def key(self) -> str:
        chosen = {column: sorted(map(str, values)) for column, values in self.slice_filter.items()}
        return json.dumps(
            [
                self.dataset,
                list(self.dimension_columns),
                sorted(chosen.items()),
                self.history_start,
                self.history_end,
                self.minimum_history,
                self.target_column,
            ]
        )


class _Recent:
    """Keeps the most recently used values and drops the rest."""

    def __init__(self, size: int) -> None:
        self._size = size
        self._items: OrderedDict[str, Any] = OrderedDict()

    def get(self, key: str) -> Any | None:
        if key in self._items:
            self._items.move_to_end(key)
            return self._items[key]
        return None

    def clear(self) -> None:
        self._items.clear()

    def put(self, key: str, value: Any) -> None:
        self._items[key] = value
        self._items.move_to_end(key)
        while len(self._items) > self._size:
            self._items.popitem(last=False)


class Workspace:
    """The data lake, the model and the caches behind the web server."""

    def __init__(self, connect: Callable[[], Any], forecaster: Forecaster) -> None:
        self._connect = connect
        self.forecaster = forecaster
        self.state = "starting"
        self.error: str | None = None
        self._store: Any = None
        # Spark is used by one thread at a time. The caches are guarded by the same lock.
        self._lock = threading.RLock()
        # Held by a job for as long as it calls the model, so that a chart does
        # not send the model a request in between.
        self._model_lock = threading.Lock()
        self._datasets: dict[str, DatasetRecord] = {}
        self._forecasts: list[ForecastRecord] = []
        self._sizes: dict[str, int] = {}
        self._panels = _Recent(6)
        self._values = _Recent(40)
        self._score_cache = _Recent(8)
        self._series_tables = _Recent(8)
        self._bands: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        self._sources: dict[str, dict[str, Any]] = {}
        self._dates: dict[str, pd.DatetimeIndex] = {}
        self._dates_read: dict[str, float] = {}
        self._dimension_values: dict[tuple[str, str], list[Any]] = {}

    # Starting up

    def start(self) -> None:
        """Connect to the data lake in the background, so the server answers at once."""
        threading.Thread(target=self.connect, name="u4cast-connect", daemon=True).start()

    def connect(self) -> None:
        try:
            store = self._connect()
            store.create_tables()
            datasets = {record.dataset: record for record in store.datasets()}
            forecasts = store.forecasts()
            sizes: dict[str, int] = {}
            for name in {record.dataset for record in forecasts}:
                sizes.update(store.forecast_sizes(name))
            with self._lock:
                self._store, self._datasets, self._forecasts, self._sizes = (
                    store,
                    datasets,
                    forecasts,
                    sizes,
                )
                self.state = "ready"
            logger.info("Connected: %d datasets, %d forecasts", len(datasets), len(forecasts))
        except Exception as error:
            logger.exception("Could not connect to the data lake")
            self.error = str(error) or type(error).__name__
            self.state = "failed"

    @property
    def store(self) -> Any:
        if self.state != "ready":
            raise NotReadyError(
                self.error
                if self.state == "failed"
                else "The connection to the data lake is still starting."
            )
        return self._store

    def status(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "error": self.error,
            "model": self.forecaster.model_name,
            "database": getattr(self._store, "database", None),
        }

    # Datasets

    def datasets(self) -> list[dict[str, Any]]:
        self.store  # noqa: B018 - raises when not ready
        return [self._dataset_json(record) for record in sorted(self._datasets.values(), key=_name)]

    def dataset(self, name: str) -> DatasetRecord:
        self.store  # noqa: B018
        if name not in self._datasets:
            raise UserError(f"There is no dataset named {name}.")
        return self._datasets[name]

    def _dataset_json(self, record: DatasetRecord) -> dict[str, Any]:
        mine = [
            f for f in self._forecasts if f.dataset == record.dataset and f.kind is Kind.FORECAST
        ]
        return {
            "name": record.dataset,
            "source_table": record.source_table,
            "date_column": record.date_column,
            "target_column": record.target_column,
            "dimension_columns": list(record.dimension_columns),
            "frequency": record.frequency.value,
            "fill": record.fill.value,
            "forecasts": len({f.forecast_name for f in mine}),
            "issues": len(mine),
        }

    def number_columns(self, record: DatasetRecord) -> list[str]:
        """Return the columns of a dataset that can be forecast: its numbers, the usual one first."""
        taken = {record.date_column, *record.dimension_columns}
        numeric = [
            column["name"]
            for column in self.source(record.source_table)["columns"]
            if column["type"].startswith(NUMBER_TYPES) and column["name"] not in taken
        ]
        return sorted(set(numeric) | {record.target_column}, key=lambda c: c != record.target_column)

    def target(self, record: DatasetRecord, chosen: str | None) -> str:
        """Return the column to forecast: the one asked for, or the dataset's usual one."""
        if not chosen or chosen == record.target_column:
            return record.target_column
        if chosen not in self.number_columns(record):
            raise UserError(f"{chosen} is not a number column of {record.dataset}.")
        return chosen

    def source(self, table: str) -> dict[str, Any]:
        """Return what a history table holds: its columns, its size and its first rows."""
        if table not in self._sources:
            with self._lock:
                try:
                    columns = self.store.columns(table)
                    preview = self.store.preview(table, 12)
                    rows = self.store.row_count(table)
                except NotReadyError:
                    raise
                except Exception as error:
                    raise UserError(f"The table {table} could not be read: {error}") from error
            self._sources[table] = {
                "table": table,
                "columns": [{"name": name, "type": kind} for name, kind in columns],
                "rows": rows,
                "preview": [
                    {column: plain(value) for column, value in row.items()}
                    for row in preview.to_dict("records")
                ],
            }
        return self._sources[table]

    def dates(self, record: DatasetRecord) -> pd.DatetimeIndex:
        """Return the dates a dataset has data for, in order."""
        read = self._dates_read.get(record.dataset)
        if read is None or time.monotonic() - read > DATES_FRESH_SECONDS:
            with self._lock:
                found = self.store.distinct_dates(record.source_table, record.date_column)
            dates = pd.DatetimeIndex(pd.to_datetime(found)).sort_values()
            before = self._dates.get(record.dataset)
            if before is not None and not before.equals(dates):
                # The table got new rows: what was read from it is out of date.
                self._panels.clear()
                self._sources.pop(record.source_table, None)
            self._dates[record.dataset] = dates
            self._dates_read[record.dataset] = time.monotonic()
        return self._dates[record.dataset]

    def dataset_overview(self, name: str) -> dict[str, Any]:
        record = self.dataset(name)
        dates = self.dates(record)
        return {
            **self._dataset_json(record),
            "source": self.source(record.source_table),
            "number_columns": self.number_columns(record),
            "dates": [iso(date) for date in dates],
            "minimum_history": minimum_history(record.frequency),
            "minimum_history_floor": min(minimum_history(record.frequency), 32),
            "forecast_groups": self.forecast_groups(name),
            "values_table": f"{self.store.database}.{forecast_values_table(name)}",
        }

    def dimension_values(self, name: str, column: str) -> list[Any]:
        record = self.dataset(name)
        if column not in record.dimension_columns:
            raise UserError(f"{column} is not a dimension column of {name}.")
        key = (name, column)
        if key not in self._dimension_values:
            with self._lock:
                found = self.store.dimension_values(record.source_table, column)
            self._dimension_values[key] = [plain(value) for value in found]
        return self._dimension_values[key]

    def source_tables(self) -> list[dict[str, Any]]:
        """Return the history tables of the application's database, and whether each is in use."""
        with self._lock:
            tables = self.store.tables()
        used = {record.source_table: record.dataset for record in self._datasets.values()}
        return [{"table": table, "dataset": used.get(table)} for table in tables]

    def profile(self, table: str, date_column: str | None = None) -> dict[str, Any]:
        """Describe a table for the screen that picks its columns, and suggest the roles."""
        source = self.source(table)
        columns = source["columns"]
        names = [column["name"] for column in columns]
        if date_column is not None and date_column not in names:
            raise UserError(f"The table has no column named {date_column}.")
        dated = [c["name"] for c in columns if c["type"].startswith(("date", "timestamp"))]
        texts = [c["name"] for c in columns if c["type"] == "string"]
        for name in texts:
            sample = [row[name] for row in source["preview"] if row.get(name) is not None]
            parsed = pd.to_datetime(pd.Series(sample, dtype=object), errors="coerce")
            if sample and parsed.notna().all() and any(ch in str(sample[0]) for ch in "-/"):
                dated.append(name)
        numeric = [c["name"] for c in columns if c["type"].startswith(NUMBER_TYPES)]
        chosen_date = date_column or (dated[0] if dated else None)
        hinted = [name for name in numeric if any(hint in name.lower() for hint in TARGET_HINTS)]
        target = (hinted or numeric or [None])[0]
        result: dict[str, Any] = {
            **source,
            "date_columns": dated,
            "number_columns": numeric,
            "suggested": {
                "date_column": chosen_date,
                "target_column": target,
                "dimension_columns": [name for name in texts if name != chosen_date],
            },
            "frequency": None,
            "frequency_share": 0.0,
            "first_date": None,
            "last_date": None,
            "periods": 0,
        }
        if chosen_date:
            with self._lock:
                found = self.store.distinct_dates(table, chosen_date)
            days = pd.DatetimeIndex(pd.to_datetime(pd.Series(found, dtype=object), errors="coerce"))
            days = days[~days.isna()].sort_values()
            guess = detect_frequency(days)
            result.update(
                frequency=guess.frequency.value if guess.frequency else None,
                frequency_share=round(guess.share, 3),
                first_date=iso(days[0]) if len(days) else None,
                last_date=iso(days[-1]) if len(days) else None,
                periods=len(days),
            )
        return result

    def save_dataset(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        name = str(payload.get("name", "")).strip()
        if not name or not slug(name):
            raise UserError("Give the dataset a name with at least one letter or digit.")
        if name in self._datasets:
            raise UserError(f"A dataset named {name} is already saved. Choose another name.")
        table = str(payload.get("source_table", ""))
        names = [column["name"] for column in self.source(table)["columns"]]
        dimensions = tuple(payload.get("dimension_columns") or ())
        chosen = [payload.get("date_column"), payload.get("target_column"), *dimensions]
        missing = [column for column in chosen if column not in names]
        if missing:
            raise UserError(f"The table has no column named {', '.join(map(str, missing))}.")
        if len(set(chosen)) != len(chosen):
            raise UserError("The date, the target and the dimensions must be different columns.")
        try:
            record = DatasetRecord(
                dataset=name,
                source_table=table,
                date_column=payload["date_column"],
                target_column=payload["target_column"],
                dimension_columns=dimensions,
                frequency=Frequency(payload.get("frequency")),
                fill=Fill(payload.get("fill", Fill.MISSING.value)),
            )
        except ValueError as error:
            raise UserError(
                f"The frequency or the fill is not one this application knows: {error}"
            ) from error
        forecast_values_table(name)  # refuses a name no table can be named by
        with self._lock:
            self.store.save_dataset(record)
            self._datasets[name] = record
        return self._dataset_json(record)

    def upload(self, filename: str, content: bytes) -> dict[str, Any]:
        """Store an uploaded CSV as a new table and return its name."""
        if len(content) > MAX_UPLOAD_BYTES:
            raise UserError(
                "The file is larger than 200 MB. Load it into an Iceberg table instead."
            )
        try:
            frame = pd.read_csv(io.BytesIO(content))
        except Exception as error:
            raise UserError(f"The file could not be read as CSV: {error}") from error
        if frame.empty or len(frame.columns) < 2:
            raise UserError("The file needs a header row, a date column and a value column.")
        frame.columns = [
            slug(str(column)) or f"column_{i}" for i, column in enumerate(frame.columns)
        ]
        if len(set(frame.columns)) != len(frame.columns):
            raise UserError("Two columns of the file have the same name.")
        for column in frame.columns:
            if frame[column].dtype == object:
                parsed = pd.to_datetime(frame[column], errors="coerce")
                if parsed.notna().sum() == frame[column].notna().sum() > 0:
                    frame[column] = parsed.dt.date
        table = f"upload_{slug(filename.rsplit('.', 1)[0]) or 'file'}"
        with self._lock:
            existing = {name.split(".")[-1] for name in self.store.tables()}
            candidate, suffix = table, 2
            while candidate in existing:
                candidate, suffix = f"{table}_{suffix}", suffix + 1
            full_name = self.store.create_table_from_frame(candidate, frame)
        return {"table": full_name, "rows": len(frame)}

    # Slices

    def panel(self, spec: SliceSpec) -> Panel:
        """Load and prepare the series of a slice. Kept in memory for the next use."""
        cached = self._panels.get(spec.key())
        if cached is not None:
            return cached
        record = self.dataset(spec.dataset)
        unknown = [c for c in spec.dimension_columns if c not in record.dimension_columns]
        unknown += [c for c in spec.slice_filter if c not in record.dimension_columns]
        if unknown:
            raise UserError(f"{', '.join(unknown)} is not a dimension column of {spec.dataset}.")
        target = self.target(record, spec.target_column)
        window = {
            "date_column": record.date_column,
            "slice_filter": spec.slice_filter,
            "history_start": spec.history_start,
            "history_end": spec.history_end,
        }
        with self._lock:
            rows = self.store.count_slice_rows(record.source_table, **window)
            if rows == 0:
                raise UserError("The filter and the dates leave no rows. Widen one of them.")
            if rows > MAX_SLICE_ROWS:
                raise UserError(
                    f"This slice reads {rows:,} rows of history, more than the {MAX_SLICE_ROWS:,} "
                    "the application loads at once. Narrow the filter."
                )
            frame = self.store.load_slice(
                record.source_table,
                target_column=target,
                dimension_columns=spec.dimension_columns,
                **window,
            )
        panel = prepare(
            frame,
            date_column=record.date_column,
            target_column=target,
            dimension_columns=spec.dimension_columns,
            frequency=record.frequency,
            minimum_history_override=spec.minimum_history,
            fill=record.fill,
        )
        if panel.n_series > MAX_SERIES:
            raise UserError(
                f"This slice has {panel.n_series:,} series, more than the {MAX_SERIES:,} one "
                "forecast takes. Narrow the filter or sum up to fewer columns."
            )
        self._panels.put(spec.key(), panel)
        return panel

    def slice_summary(
        self, spec: SliceSpec, horizon: int, cutoffs: int, cutoff_step: int
    ) -> dict[str, Any]:
        """Describe a slice before it is forecast: what is kept, left out, and how long it takes."""
        panel = self.panel(spec)
        lowest = panel.minimum_history
        season = SEASON_LENGTH[panel.frequency]
        starts = [s for s in range(0, panel.n_steps - lowest + 1, season)] or [0]
        by_start = [
            {
                "start": iso(panel.dates[start]),
                "steps": panel.n_steps - start,
                "series": int(
                    (panel.n_steps - np.maximum(panel.first_steps, start) >= lowest).sum()
                ),
            }
            for start in starts[:8]
        ]
        left_out = panel.left_out
        planned = self._cutoff_steps(panel, horizon, cutoffs, cutoff_step)
        return {
            "series": panel.n_series,
            "steps": panel.n_steps,
            "history_start": iso(panel.dates[0]),
            "history_end": iso(panel.dates[-1]),
            "minimum_history": lowest,
            "left_out": len(left_out),
            "left_out_by_reason": {
                str(k): int(v) for k, v in left_out["reason"].value_counts().items()
            },
            "left_out_sample": [
                {
                    "label": _label(row, panel.dimension_columns),
                    "reason": row["reason"],
                    "first_date": iso(row["first_date"]),
                    "steps": int(row["steps"]),
                }
                for row in left_out.head(30).to_dict("records")
            ],
            "rows_off_grid": panel.rows_off_grid,
            "values_filled": int((np.isnan(panel.observed) & ~np.isnan(panel.values)).sum()),
            "values_missing": int(
                (
                    np.isnan(panel.values)
                    & (np.arange(panel.n_steps) >= panel.first_steps[:, None])
                ).sum()
            ),
            "by_start": by_start,
            "sample": [
                _label(row, panel.dimension_columns)
                for row in panel.series.head(6).to_dict("records")
            ],
            "seconds": round(self._seconds(panel.n_series, panel.n_steps, horizon), 1),
            "backtest_cutoffs": [iso(panel.dates[k - 1]) for k in planned],
            "backtest_seconds": round(
                sum(self._seconds(panel.n_series, k, horizon) for k in planned), 1
            ),
            "default_horizon": DEFAULT_HORIZON[panel.frequency],
        }

    @staticmethod
    def _seconds(series: int, steps: int, horizon: int) -> float:
        blocks = math.ceil(steps / BLOCK_STEPS) + math.ceil(horizon / BLOCK_STEPS)
        requests = math.ceil(series * blocks / 2000)
        return series * blocks * SECONDS_PER_SERIES_BLOCK + requests * SECONDS_PER_REQUEST

    @staticmethod
    def _cutoff_steps(panel: Panel, horizon: int, cutoffs: int, step: int) -> list[int]:
        """Return the history lengths of a backtest's cutoffs: the latest ones that fit."""
        last = panel.n_steps - horizon
        found = [last - i * step for i in range(max(0, cutoffs))]
        return sorted(k for k in found if k >= panel.minimum_history)

    # Forecasts: lists

    def _issues(self, dataset: str, name: str, kind: Kind) -> list[ForecastRecord]:
        """Return the issues of a forecast, oldest history first, one per end of history."""
        found: dict[dt.date, ForecastRecord] = {}
        for record in sorted(self._forecasts, key=lambda r: r.created_at):
            if record.dataset == dataset and record.forecast_name == name and record.kind is kind:
                found[record.history_end_date] = record
        return [found[day] for day in sorted(found)]

    def forecast_groups(self, dataset: str) -> list[dict[str, Any]]:
        names = sorted({r.forecast_name for r in self._forecasts if r.dataset == dataset})
        groups = []
        for name in names:
            issues = self._issues(dataset, name, Kind.FORECAST)
            if not issues:
                continue
            latest = issues[-1]
            groups.append(
                {
                    "forecast_name": name,
                    "issues": len(issues),
                    "backtests": len(self._issues(dataset, name, Kind.BACKTEST)),
                    "latest": self._record_json(latest),
                }
            )
        return sorted(groups, key=lambda g: g["latest"]["created_at"], reverse=True)

    def record(self, forecast_id: str) -> ForecastRecord:
        self.store  # noqa: B018
        for record in self._forecasts:
            if record.forecast_id == forecast_id:
                return record
        raise UserError("There is no forecast with that identifier.")

    def _record_json(self, record: ForecastRecord) -> dict[str, Any]:
        return {
            "forecast_id": record.forecast_id,
            "forecast_name": record.forecast_name,
            "dataset": record.dataset,
            "target_column": record.target_column,
            "dimension_columns": list(record.dimension_columns),
            "slice_filter": {k: list(v) for k, v in record.slice_filter.items()},
            "frequency": record.frequency.value,
            "history_start_date": iso(record.history_start_date),
            "history_end_date": iso(record.history_end_date),
            "horizon": record.horizon,
            "model_name": record.model_name,
            "kind": record.kind.value,
            "created_at": record.created_at.isoformat(),
            "fill": record.fill.value,
            "series": self._sizes.get(record.forecast_id),
        }

    def forecast_overview(self, dataset: str, name: str) -> dict[str, Any]:
        """Return a forecast by name: its issues, newest first, and what can be done next."""
        record = self.dataset(dataset)
        issues = self._issues(dataset, name, Kind.FORECAST)
        if not issues:
            raise UserError(f"There is no forecast named {name} for {dataset}.")
        latest = issues[-1]
        dates = self.dates(record)
        issued = {issue.history_end_date for issue in issues}
        lowest = latest.minimum_history or minimum_history(record.frequency)
        start = pd.Timestamp(latest.history_start_date)
        open_dates = [
            day for day in dates[dates >= start][lowest - 1 :] if day.date() not in issued
        ]
        return {
            "dataset": self._dataset_json(record),
            "forecast_name": name,
            "issues": [self._record_json(issue) for issue in reversed(issues)],
            "backtests": [self._record_json(b) for b in self._issues(dataset, name, Kind.BACKTEST)],
            "reissue_dates": [iso(day) for day in open_dates],
            "latest_data_date": iso(dates[-1]) if len(dates) else None,
            "values_table": f"{self.store.database}.{forecast_values_table(dataset)}",
            "forecasts_table": f"{self.store.database}.forecasts",
        }

    def values(self, record: ForecastRecord) -> pd.DataFrame:
        """Return the stored rows of a forecast, by series and step."""
        cached = self._values.get(record.forecast_id)
        if cached is None:
            with self._lock:
                cached = self.store.forecast_values(record.dataset, [record.forecast_id])
            cached = cached.sort_values([TS_ID, "step"], ignore_index=True)
            self._values.put(record.forecast_id, cached)
            self._sizes[record.forecast_id] = cached[TS_ID].nunique()
        return cached

    def _panel_of(self, record: ForecastRecord, end: dt.date | None = None) -> Panel:
        """Return the slice a forecast was made for, with history to `end` or to the latest data."""
        return self.panel(
            SliceSpec(
                dataset=record.dataset,
                dimension_columns=record.dimension_columns,
                slice_filter=record.slice_filter,
                history_start=iso(record.history_start_date),
                history_end=iso(end),
                minimum_history=record.minimum_history or None,
                target_column=record.target_column,
            )
        )

    # Forecasts: making them

    def _save(self, issued: IssuedForecast, dataset: DatasetRecord) -> None:
        with self._lock:
            self.store.save_forecast(issued, dataset)
            self._forecasts.append(issued.record)
            self._sizes[issued.record.forecast_id] = len(issued.rows)
        values = issued.values.copy()
        for column in dataset.dimension_columns:
            values[column] = (
                values[column].astype("string").astype(object).where(values[column].notna(), None)
                if column in values.columns
                else None
            )
        values["date"] = pd.to_datetime(values["date"])
        # The same columns in the same order as the stored table.
        values = values[list(forecast_values_columns(dataset.dimension_columns))]
        self._values.put(issued.record.forecast_id, values)

    def _with_progress(
        self, job: Job, stage: str, low: float, high: float, work: Callable[[], Any]
    ) -> Any:
        """Run one model call and move the job's progress from `low` to `high` as batches finish."""
        job.report(stage, low)
        if hasattr(self.forecaster, "on_batch"):
            self.forecaster.on_batch = lambda done, total: job.report(
                stage, low + (high - low) * done / max(total, 1)
            )
        try:
            return work()
        finally:
            if hasattr(self.forecaster, "on_batch"):
                self.forecaster.on_batch = None

    def _backtest(
        self,
        job: Job,
        panel: Panel,
        like: ForecastRecord,
        steps: list[int],
        low: float,
        high: float,
    ) -> int:
        dataset = self.dataset(like.dataset)
        for index, history_steps in enumerate(steps):
            share = (high - low) / len(steps)
            issued = self._with_progress(
                job,
                f"Checking accuracy from {panel.dates[history_steps - 1].date()}",
                low + index * share,
                low + (index + 0.9) * share,
                lambda k=history_steps: issue_forecast(
                    panel,
                    self.forecaster,
                    horizon=like.horizon,
                    forecast_name=like.forecast_name,
                    dataset=like.dataset,
                    target_column=like.target_column,
                    slice_filter=like.slice_filter,
                    kind=Kind.BACKTEST,
                    history_steps=k,
                ),
            )
            self._save(issued, dataset)
        return len(steps)

    def check_new_forecast(self, dataset: str, forecast_name: str, horizon: int) -> str:
        """Refuse a new forecast that cannot be made, before any work starts. Return its name."""
        self.dataset(dataset)
        name = forecast_name.strip()
        if not name:
            raise UserError("Give the forecast a name.")
        if any(r.dataset == dataset and r.forecast_name == name for r in self._forecasts):
            raise UserError(
                f"A forecast named {name} exists for this dataset. Open it to reissue it, "
                "or choose another name."
            )
        if not 1 <= horizon <= 1024:
            raise UserError("The horizon must be between 1 and 1,024 periods.")
        return name

    def create_forecast(
        self,
        job: Job,
        spec: SliceSpec,
        forecast_name: str,
        horizon: int,
        cutoffs: int,
        cutoff_step: int,
    ) -> dict[str, Any]:
        dataset = self.dataset(spec.dataset)
        name = self.check_new_forecast(spec.dataset, forecast_name, horizon)
        job.report("Loading history from the data lake", 0.03)
        panel = self.panel(spec)
        if panel.n_series == 0:
            raise UserError(
                f"No series has the minimum history of {panel.minimum_history} periods. "
                "Widen the dates or the filter."
            )
        steps = self._cutoff_steps(panel, horizon, cutoffs, cutoff_step)
        forecast_share = 0.85 / (1 + len(steps))
        with self._model_lock:
            issued = self._with_progress(
                job,
                f"Forecasting {panel.n_series:,} series",
                0.1,
                0.1 + forecast_share,
                lambda: issue_forecast(
                    panel,
                    self.forecaster,
                    horizon=horizon,
                    forecast_name=name,
                    dataset=dataset.dataset,
                    target_column=self.target(dataset, spec.target_column),
                    slice_filter=spec.slice_filter,
                ),
            )
            job.report("Saving the forecast to Iceberg", 0.1 + forecast_share)
            self._save(issued, dataset)
            if steps:
                self._backtest(job, panel, issued.record, steps, 0.12 + forecast_share, 0.98)
        return {
            "dataset": dataset.dataset,
            "forecast_name": name,
            "forecast_id": issued.record.forecast_id,
            "series": len(issued.rows),
            "rows": len(issued.values),
            "backtests": len(steps),
        }

    def reissue(self, job: Job, dataset: str, name: str, as_of: str | None) -> dict[str, Any]:
        """Issue a forecast again with history to `as_of`, or to the latest data."""
        record = self.dataset(dataset)
        issues = self._issues(dataset, name, Kind.FORECAST)
        if not issues:
            raise UserError(f"There is no forecast named {name} for {dataset}.")
        latest = issues[-1]
        job.report("Loading history from the data lake", 0.03)
        end = pd.Timestamp(as_of).date() if as_of else None
        panel = self._panel_of(latest, end)
        if panel.n_series == 0:
            raise UserError("No series has the minimum history by that date. Choose a later one.")
        ends = panel.dates[-1].date()
        if any(issue.history_end_date == ends for issue in issues):
            raise UserError(
                f"This forecast was already issued with history to {ends}. "
                "There is no newer data to reissue it with."
            )
        with self._model_lock:
            issued = self._with_progress(
                job,
                f"Forecasting {panel.n_series:,} series with history to {ends}",
                0.1,
                0.9,
                lambda: reissue_forecast(latest, panel, self.forecaster),
            )
            job.report("Saving the forecast to Iceberg", 0.92)
            self._save(issued, record)
        return {
            "dataset": dataset,
            "forecast_name": name,
            "forecast_id": issued.record.forecast_id,
            "series": len(issued.rows),
            "rows": len(issued.values),
            "backtests": 0,
        }

    def check_accuracy(
        self, job: Job, dataset: str, name: str, cutoffs: int, cutoff_step: int
    ) -> dict[str, Any]:
        """Backtest a forecast: forecast its slice from past cutoffs and store the results."""
        issues = self._issues(dataset, name, Kind.FORECAST)
        if not issues:
            raise UserError(f"There is no forecast named {name} for {dataset}.")
        latest = issues[-1]
        job.report("Loading history from the data lake", 0.03)
        panel = self._panel_of(latest)
        steps = self._cutoff_steps(panel, latest.horizon, cutoffs, cutoff_step)
        done = {b.history_end_date for b in self._issues(dataset, name, Kind.BACKTEST)}
        steps = [k for k in steps if panel.dates[k - 1].date() not in done]
        if not steps:
            raise UserError(
                "There is no new cutoff to check: the history is too short for this horizon, "
                "or these cutoffs were already checked."
            )
        with self._model_lock:
            count = self._backtest(job, panel, latest, steps, 0.08, 0.98)
        return {"dataset": dataset, "forecast_name": name, "backtests": count}

    # Forecasts: looking at them

    def _row_of(self, panel: Panel, ts_id: str) -> int:
        try:
            return panel.ts_ids.index(ts_id)
        except ValueError:
            raise UserError("That series is not part of this forecast.") from None

    def _band_of_total(
        self, record: ForecastRecord, panel: Panel, rows: np.ndarray, history_steps: int
    ) -> tuple[np.ndarray, np.ndarray] | None:
        """Return the low and high of the total as shares of its forecast.

        The low and high of single series cannot be added up: their sum is far
        wider than the range of the total. So the total's own history is
        forecast once, and its range is taken from that.
        """
        if record.forecast_id in self._bands:
            return self._bands[record.forecast_id]
        if not self._model_lock.acquire(blocking=False):
            return None  # a job is using the model; the range appears once it is done
        try:
            history = np.nansum(panel.history(history_steps)[rows], axis=0)[None, :]
            result = self.forecaster.forecast(history, record.horizon)
        except Exception:
            logger.warning("Could not forecast the total of %s", record.forecast_id, exc_info=True)
            return None
        finally:
            self._model_lock.release()
        middle = np.where(result.forecast[0] > 0, result.forecast[0], np.nan)
        band = (result.low[0] / middle, result.high[0] / middle)
        self._bands[record.forecast_id] = band
        return band

    def chart(self, forecast_id: str, ts_id: str | None) -> dict[str, Any]:
        """Return history, forecast and range of one series, or of the total of all series."""
        record = self.record(forecast_id)
        values = self.values(record)
        panel = self._panel_of(record)
        history_steps = int(panel.calendar.steps_of([record.history_end_date])[0]) + 1
        horizon = record.horizon
        note = None
        if ts_id:
            row = self._row_of(panel, ts_id)
            mine = values[values[TS_ID] == ts_id].sort_values("step")
            if mine.empty:
                raise UserError("That series is not part of this forecast.")
            actual = panel.values[row]
            forecast, low, high = (mine[c].to_numpy() for c in ("forecast", "low", "high"))
            label = _label(panel.series.iloc[row].to_dict(), panel.dimension_columns)
            count = 1
        else:
            ids = values[TS_ID].unique()
            rows = pd.Index(panel.ts_ids).get_indexer(ids)
            rows = rows[rows >= 0]
            actual = _sum(panel.values[rows])
            forecast = values.groupby("step")["forecast"].sum().sort_index().to_numpy()
            band = self._band_of_total(record, panel, rows, history_steps)
            if band is None:
                low = high = np.full(horizon, np.nan)
                note = "The range of the total is not available right now."
            else:
                low, high = forecast * band[0], forecast * band[1]
            label = f"Total of {len(ids):,} series"
            count = len(ids)
        after = np.full(horizon, np.nan)
        known = actual[history_steps : history_steps + horizon]
        after[: len(known)] = known
        return {
            "forecast_id": forecast_id,
            "ts_id": ts_id,
            "label": label,
            "series": count,
            "target": record.target_column,
            "dates": [iso(day) for day in panel.dates[:history_steps]],
            "actual": numbers(actual[:history_steps]),
            "future_dates": [
                iso(day) for day in panel.calendar.future_dates(history_steps, horizon)
            ],
            "forecast": numbers(forecast),
            "low": numbers(low),
            "high": numbers(high),
            "actual_after": numbers(after),
            "note": note,
        }

    def _series_table(self, record: ForecastRecord) -> pd.DataFrame:
        cached = self._series_tables.get(record.forecast_id)
        if cached is not None:
            return cached
        values = self.values(record)
        panel = self._panel_of(record)
        history_steps = int(panel.calendar.steps_of([record.history_end_date])[0]) + 1
        season = SEASON_LENGTH[record.frequency]
        table = (
            values.groupby(TS_ID, sort=True)["forecast"].sum().rename("forecast_sum").reset_index()
        )
        rows = pd.Index(panel.ts_ids).get_indexer(table[TS_ID])
        earlier = np.full(len(table), np.nan)
        start = history_steps - season
        if start >= 0 and record.horizon <= season:
            # The same periods one season earlier, for "against a year ago".
            found = rows >= 0
            earlier[found] = np.nansum(
                panel.values[rows[found], start : start + record.horizon], axis=1
            )
        table["previous_sum"] = earlier
        table["change"] = np.where(earlier > 0, table["forecast_sum"] / earlier - 1, np.nan)
        series = panel.series.set_index(TS_ID).reindex(table[TS_ID])
        for column in record.dimension_columns:
            table[column] = series[column].astype("string").to_numpy()
        table["label"] = [_label(row, record.dimension_columns) for row in table.to_dict("records")]
        table["search"] = table["label"].str.lower()
        self._series_tables.put(record.forecast_id, table)
        return table

    def series(
        self, forecast_id: str, query: str, sort: str, limit: int, offset: int
    ) -> dict[str, Any]:
        """Return the series of a forecast: searched, sorted, and one page of them."""
        record = self.record(forecast_id)
        table = _search(self._series_table(record), query)
        if sort == "name":
            table = table.sort_values("label")
        elif sort in ("growth", "decline"):
            table = table.sort_values("change", ascending=sort == "decline", na_position="last")
        else:
            table = table.sort_values("forecast_sum", ascending=False)
        page = table.iloc[offset : offset + limit]
        return {
            "total": len(table),
            "dimension_columns": list(record.dimension_columns),
            "rows": [
                {
                    "ts_id": row[TS_ID],
                    "label": row["label"],
                    "dimensions": {c: plain(row[c]) for c in record.dimension_columns},
                    "forecast_sum": number(row["forecast_sum"]),
                    "previous_sum": number(row["previous_sum"]),
                    "change": number(row["change"]),
                }
                for row in page.to_dict("records")
            ],
        }

    def compare(self, dataset: str, name: str, ts_id: str | None, kind: str) -> dict[str, Any]:
        """Return what each issue predicted for the same dates, next to what happened."""
        issues = self._issues(dataset, name, Kind(kind))[-MAX_COMPARED:]
        if not issues:
            raise UserError(f"There is nothing to compare yet for {name}.")
        panel = self._panel_of(issues[-1])
        frames = [self.values(issue) for issue in issues]
        if ts_id:
            row = self._row_of(panel, ts_id)
            actual = panel.actuals[row]
            shown = panel.values[row]
            per_issue = [f[f[TS_ID] == ts_id].set_index("date")["forecast"] for f in frames]
            label, count = _label(panel.series.iloc[row].to_dict(), panel.dimension_columns), 1
        else:
            common = set(frames[0][TS_ID])
            for frame in frames[1:]:
                common &= set(frame[TS_ID])
            rows = pd.Index(panel.ts_ids).get_indexer(sorted(common))
            rows = rows[rows >= 0]
            actual = _sum(panel.actuals[rows])
            shown = _sum(panel.values[rows])
            per_issue = [f[f[TS_ID].isin(common)].groupby("date")["forecast"].sum() for f in frames]
            label, count = f"Total of {len(common):,} series", len(common)
        dates = sorted(set().union(*(series.index for series in per_issue)))
        steps = panel.calendar.steps_of(dates)
        known = (steps >= 0) & (steps < panel.n_steps)
        happened = np.full(len(dates), np.nan)
        happened[known] = actual[steps[known]]
        first_end = int(panel.calendar.steps_of([issues[0].history_end_date])[0])
        lead = max(0, first_end + 1 - 2 * SEASON_LENGTH[panel.frequency])
        return {
            "label": label,
            "series": count,
            "target": issues[-1].target_column,
            "issues": [self._record_json(issue) for issue in issues],
            "dates": [iso(day) for day in dates],
            "actual": numbers(happened),
            "forecasts": [numbers(series.reindex(dates).to_numpy()) for series in per_issue],
            "context_dates": [iso(day) for day in panel.dates[lead : first_end + 1]],
            "context_actual": numbers(shown[lead : first_end + 1]),
        }

    def _scores(self, dataset: str, name: str) -> tuple[Scores, list[ForecastRecord]] | None:
        backtests = self._issues(dataset, name, Kind.BACKTEST)
        if not backtests:
            return None
        key = ",".join(record.forecast_id for record in backtests)
        cached = self._score_cache.get(key)
        if cached is None:
            panel = self._panel_of(backtests[-1])
            cached = score_forecasts(panel, [(record, self.values(record)) for record in backtests])
            self._score_cache.put(key, cached)
        return cached, backtests

    def accuracy(self, dataset: str, name: str, query: str, limit: int) -> dict[str, Any]:
        """Return the backtest error of a forecast: in total, by periods ahead, and by series."""
        self.dataset(dataset)
        found = self._scores(dataset, name)
        if found is None:
            return {"available": False}
        scores, backtests = found
        latest = backtests[-1]
        table = scores.by_series.copy()
        table["label"] = [_label(row, latest.dimension_columns) for row in table.to_dict("records")]
        table["search"] = table["label"].str.lower()
        table = _search(table, query)
        measures = ("mae", "rmse", "mape", "wape", "reference_mae", "reference_wape")
        return {
            "available": True,
            "cutoffs": [iso(record.history_end_date) for record in backtests],
            "horizon": latest.horizon,
            "target": latest.target_column,
            "season": SEASON_LENGTH[latest.frequency],
            "total": {key: number(value) for key, value in scores.total.items()},
            "by_step": [
                {key: plain(value) for key, value in row.items()}
                for row in scores.by_step.to_dict("records")
            ],
            "series": len(table),
            "by_series": [
                {
                    "ts_id": row[TS_ID],
                    "label": row["label"],
                    "n": int(row["n"]),
                    **{measure: number(row[measure]) for measure in measures},
                }
                for row in table.head(limit).to_dict("records")
            ],
        }

    def stored_rows(self, forecast_id: str, limit: int) -> dict[str, Any]:
        """Return the first rows of a forecast as stored, and where they are stored."""
        record = self.record(forecast_id)
        values = self.values(record)
        table = f"{self.store.database}.{forecast_values_table(record.dataset)}"
        return {
            "table": table,
            "forecasts_table": f"{self.store.database}.forecasts",
            "rows": len(values),
            "columns": list(values.columns),
            "preview": [
                {column: plain(value) for column, value in row.items()}
                for row in values.head(limit).to_dict("records")
            ],
            "sql": f"select *\nfrom {table}\nwhere forecast_id = '{record.forecast_id}'\n"
            "order by ts_id, step;",
            "record_sql": f"select *\nfrom {self.store.database}.forecasts\n"
            f"where forecast_name = '{record.forecast_name.replace(chr(39), chr(39) * 2)}'\n"
            "order by history_end_date;",
        }

    def csv(self, forecast_id: str) -> tuple[str, str]:
        """Return a forecast's stored rows as CSV text, and a file name for them."""
        record = self.record(forecast_id)
        values = self.values(record).copy()
        values["date"] = values["date"].dt.strftime("%Y-%m-%d")
        name = f"{slug(record.forecast_name)}_{record.history_end_date}_{record.kind.value}.csv"
        return values.to_csv(index=False), name


DEFAULT_HORIZON = {
    Frequency.DAILY: 28,
    Frequency.WEEKLY: 13,
    Frequency.MONTHLY: 12,
    Frequency.QUARTERLY: 4,
}


def _name(record: DatasetRecord) -> str:
    return record.dataset.lower()


def _label(row: Mapping[str, Any], dimension_columns: Sequence[str]) -> str:
    """Name a series by its dimension values, the most specific last."""
    parts = [str(row[c]) for c in dimension_columns if row.get(c) is not None and row[c] == row[c]]
    return " · ".join(parts) if parts else "All data"


def _sum(values: np.ndarray) -> np.ndarray:
    """Add up series date by date. A date on which no series has a value stays missing."""
    total = np.nansum(values, axis=0)
    return np.where(np.isnan(values).all(axis=0), np.nan, total)


def _search(table: pd.DataFrame, query: str) -> pd.DataFrame:
    for word in query.lower().split():
        table = table[table["search"].str.contains(word, regex=False)]
    return table
