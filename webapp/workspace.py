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
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from u4cast.backtest import REFERENCE, Scores, score_forecasts
from u4cast.connections import Catalog
from u4cast.endpoint import BLOCK_STEPS
from u4cast.forecast import IssuedForecast, issue_forecast, reissue_forecast
from u4cast.frequency import SEASON_LENGTH, Frequency, detect_frequency, minimum_history
from u4cast.layout import (
    DatasetRecord,
    ForecastRecord,
    Kind,
    forecast_table,
    forecast_values_columns,
    forecast_values_table,
)
from u4cast.model import Forecaster
from u4cast.prepare import TS_ID, Fill, Panel, prepare
from u4cast.production import (
    ACTIVE,
    DATASET_VARIABLE,
    FAILED,
    FORECAST_VARIABLE,
    ISSUED,
    NO_NEW_DATA,
    PAUSED,
    RETIRED,
    Schedule,
    Scheduler,
    checked_cron,
    job_name,
    next_run,
)
from u4cast.serving import ModelChoice
from u4cast.storage import OWN_TABLES
from webapp.jobs import Job

logger = logging.getLogger(__name__)

# A slice is refused above these sizes, before it is loaded.
MAX_SLICE_ROWS = 5_000_000
MAX_SERIES = 25_000
DATES_FRESH_SECONDS = 60
MODELS_FRESH_SECONDS = 60
MAX_DRILL_VALUES = 2_000
WARM = 8
BROWSE_FRESH_SECONDS = 300
STORED_FRESH_SECONDS = 20
"""How long the lists of forecasts and schedules are trusted before Iceberg is asked again."""
"""How many forecasts are read ahead of time when the application starts."""
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


class NoNewDataError(UserError):
    """A forecast cannot be issued again because no history arrived since its last issue."""


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

    def __init__(
        self,
        connect: Callable[[], Any],
        forecaster: Forecaster,
        choices: Callable[[], list[ModelChoice]] | None = None,
        catalog: Callable[[Any], Catalog] | None = None,
        scheduler: Scheduler | None = None,
    ) -> None:
        self.scheduler = scheduler
        self._schedules: dict[tuple[str, str], Schedule] = {}
        self._stored_read = 0.0
        self._refresher: threading.Thread | None = None
        self._writes = 0
        """Counts what the application itself stored or deleted."""
        self._deleted: set[str] = set()
        self._table_sizes: dict[str, dict[str, int]] = {}
        self._connect = connect
        self._make_catalog = catalog
        self._catalog: Catalog | None = None
        self._browsed = _Recent(64)
        self._default = forecaster
        self._active: Forecaster | None = None
        self._list_choices = choices
        self._choices: list[ModelChoice] = []
        self._choices_read = 0.0
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
        self._panels = _Recent(8)
        self._values = _Recent(40)
        self._score_cache = _Recent(8)
        self._series_tables = _Recent(8)
        self._bands: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        self._sources: dict[str, dict[str, Any]] = {}
        self._dates: dict[str, pd.DatetimeIndex] = {}
        self._dates_read: dict[str, float] = {}
        self._history: dict[tuple[str, str], pd.Series] = {}
        self._dimension_values: dict[tuple[str, str], list[Any]] = {}

    # Starting up

    def start(self) -> None:
        """Connect to the data lake in the background, so the server answers at once."""

        def work() -> None:
            self.connect()
            if self.state == "ready":
                self._refresher = threading.Thread(
                    target=self._keep_reading_stored, name="u4cast-refresh", daemon=True
                )
                self._refresher.start()
            self.warm()

        threading.Thread(target=work, name="u4cast-connect", daemon=True).start()

    def warm(self) -> None:
        """Read the newest issue of every forecast ahead of time, so its first chart is quick."""
        if self.state != "ready":
            return
        newest: dict[tuple[str, str], ForecastRecord] = {}
        for record in self._forecasts:
            if record.kind is Kind.FORECAST:
                key = (record.dataset, record.forecast_name)
                if key not in newest or record.created_at > newest[key].created_at:
                    newest[key] = record
        for record in sorted(newest.values(), key=lambda r: r.created_at, reverse=True)[:WARM]:
            try:
                self.chart(record.forecast_id, None)
            except Exception:
                logger.info("Could not read %s ahead of time", record.forecast_name, exc_info=True)
        try:
            self.cleanup()  # measures every forecast table once, which takes seconds
        except Exception:
            logger.info("Could not measure the forecast tables ahead of time", exc_info=True)

    def connect(self) -> None:
        try:
            store = self._connect()
            store.create_tables()
            datasets = {record.dataset: record for record in store.datasets()}
            forecasts = store.forecasts()
            sizes = store.forecast_sizes(forecasts)
            with self._lock:
                self._store, self._datasets, self._forecasts, self._sizes = (
                    store,
                    datasets,
                    forecasts,
                    sizes,
                )
                self._schedules = {(s.dataset, s.forecast_name): s for s in store.schedules()}
                self._stored_read = time.monotonic()
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

    @property
    def forecaster(self) -> Forecaster:
        """The model in use: the one a running job chose, or the default."""
        return self._active or self._default

    def choices(self) -> list[ModelChoice]:
        """Return the models a forecast can be made with, the default first."""
        fresh = time.monotonic() - self._choices_read < MODELS_FRESH_SECONDS
        if not self._choices or not fresh:
            found = self._list_choices() if self._list_choices else []
            if not found:
                key = self._default.model_name.split("/")[-1]
                found = [ModelChoice(key=key, forecaster=self._default, default=True)]
            self._choices, self._choices_read = found, time.monotonic()
        return self._choices

    def models(self) -> list[dict[str, Any]]:
        return [
            {"key": c.key, "model_name": c.model_name, "default": c.default} for c in self.choices()
        ]

    def _chosen(self, key: str | None) -> Forecaster:
        """Return the model with that key. No key means the default."""
        choices = self.choices()
        if not key:
            return next((c.forecaster for c in choices if c.default), self._default)
        for choice in choices:
            if choice.key == key:
                return choice.forecaster
        raise UserError(f"There is no model named {key} to forecast with.")

    def _model_of(self, record: ForecastRecord) -> Forecaster:
        """Return the model a stored forecast was made with, or the default if it is gone."""
        for choice in self.choices():
            if choice.model_name == record.model_name:
                return choice.forecaster
        return self._chosen(None)

    @contextmanager
    def _using(self, forecaster: Forecaster) -> Iterator[None]:
        """Hold the model for one job. Jobs run one at a time."""
        with self._model_lock:
            self._active = forecaster
            try:
                yield
            finally:
                self._active = None

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
        """Return the number columns of a dataset that can be forecast, the usual one first."""
        taken = {record.date_column, *record.dimension_columns}
        numeric = [
            column["name"]
            for column in self.source(record.source_table)["columns"]
            if column["type"].startswith(NUMBER_TYPES) and column["name"] not in taken
        ]
        others = [column for column in numeric if column != record.target_column]
        return [record.target_column, *others]

    def target(self, record: DatasetRecord, chosen: str | None) -> str:
        """Return the column to forecast: the one asked for, or the dataset's usual one."""
        if not chosen or chosen == record.target_column:
            return record.target_column
        if chosen not in self.number_columns(record):
            raise UserError(f"{chosen} is not a number column of {record.dataset}.")
        return chosen

    def source(self, table: str) -> dict[str, Any]:
        """Return what a history table holds: its columns, its size and its first rows."""
        database, _, name = table.lower().rpartition(".")
        mine = database == str(getattr(self._store, "database", "")).lower()
        if mine and (name in OWN_TABLES or name.startswith("forecast_values_")):
            raise UserError(
                f"{table} is a table this application keeps its own records in. "
                "Choose a table of history."
            )
        if table not in self._sources:
            with self._lock:
                try:
                    columns = self.store.columns(table)
                    preview = self.store.preview(table, 12)  # the screens show the first 10
                    rows = self.store.row_count(table)
                except NotReadyError:
                    raise
                except Exception as error:
                    raise UserError(_table_problem(table, error)) from error
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
                self._history.clear()
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
            "values_table": f"{self.store.database}.{forecast_values_table(name)}__<forecast>",
        }

    def history(self, name: str, target: str | None = None) -> dict[str, Any]:
        """Return the whole of a dataset added together, date by date, for one number column."""
        record = self.dataset(name)
        column = self.target(record, target)
        dates = self.dates(record)  # also drops what is out of date
        key = (name, column)
        if key not in self._history:
            with self._lock:
                frame = self.store.load_slice(
                    record.source_table,
                    date_column=record.date_column,
                    target_column=column,
                    dimension_columns=(),
                )
            totals = frame.set_index(record.date_column)[column].groupby(level=0).sum()
            self._history[key] = totals.reindex(dates)
        totals = self._history[key]
        return {
            "target": column,
            "dates": [iso(day) for day in totals.index],
            "values": [None if pd.isna(value) else float(value) for value in totals],
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

    # Browsing to a table: data connection, database, table.

    @property
    def catalog(self) -> Catalog:
        if self._make_catalog is None:
            raise UserError(
                "Browsing data connections is not available here. Type the table's name."
            )
        if self._catalog is None:
            self._catalog = self._make_catalog(self.store)
        return self._catalog

    def _browse(self, key: str, ask: Callable[[], Any]) -> Any:
        """Ask the catalog, and keep the answer for a few minutes."""
        kept = self._browsed.get(key)
        if kept is not None and time.monotonic() - kept[0] < BROWSE_FRESH_SECONDS:
            return kept[1]
        try:
            with self._lock:
                answer = ask()
        except (UserError, NotReadyError):
            raise
        except ValueError as error:
            raise UserError(str(error)) from error
        except Exception as error:
            logger.info("A data connection could not be read", exc_info=True)
            raise UserError(
                "This data connection could not be opened from the application. "
                "Choose another one: they all show the same databases."
            ) from error
        self._browsed.put(key, (time.monotonic(), answer))
        return answer

    def connections(self) -> dict[str, Any]:
        """Return the project's data connections, the data lake first, and where to start."""
        if self._make_catalog is None:
            return {"connections": [], "database": getattr(self._store, "database", None)}
        found = self._browse("connections", self.catalog.connections)
        return {
            "connections": [{"name": c.name, "type": c.type, "kind": c.kind} for c in found],
            "database": self.store.database,
        }

    def databases(self, connection: str) -> list[str]:
        return self._browse(f"databases|{connection}", lambda: self.catalog.databases(connection))

    def tables_of(self, connection: str, database: str) -> list[dict[str, Any]]:
        """Return the tables of a database, and which datasets already read each one."""
        names = self._browse(
            f"tables|{connection}|{database}", lambda: self.catalog.tables(connection, database)
        )
        used = {record.source_table: record.dataset for record in self._datasets.values()}
        mine = database == self.store.database
        return [
            {"table": f"{database}.{name}", "name": name, "dataset": used.get(f"{database}.{name}")}
            for name in names
            if not (mine and (name in OWN_TABLES or name.startswith("forecast_values_")))
        ]

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
        used = [
            r.dataset for r in self._datasets.values() if r.source_table.lower() == table.lower()
        ]
        result["used_by"] = used
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
        record = self.dataset(spec.dataset)
        # Asking for the dates first drops what was kept if the table has new rows.
        self.dates(record)
        cached = self._panels.get(spec.key())
        if cached is not None:
            return cached
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

    # Forecasts in production

    def refresh(self) -> None:
        """Read again what a scheduled job may have written: new runs, and the schedules.

        A job in production appends its run to Iceberg without telling the
        application. When the application is running, a background thread
        reads the lists again every little while, so that no page waits for
        it: reading them takes several seconds. Without that thread, as in a
        job or a test, the lists are read here when they are old.
        """
        if self._refresher is not None:
            return
        if time.monotonic() - self._stored_read >= STORED_FRESH_SECONDS:
            self._read_stored()

    def _read_stored(self) -> None:
        # Spark is asked outside the lock, so that pages are not held up meanwhile.
        # If the application itself stored or deleted something in the meantime,
        # what was read may be older than what it knows: it is thrown away, and
        # the next reading counts. A run deleted here is never taken back in.
        with self._lock:
            before = self._writes
        forecasts = self.store.forecasts()
        schedules = {(s.dataset, s.forecast_name): s for s in self.store.schedules()}
        with self._lock:
            if self._writes != before:
                return
            known = {record.forecast_id for record in self._forecasts}
            new = [
                record
                for record in forecasts
                if record.forecast_id not in known and record.forecast_id not in self._deleted
            ]
        sizes = self.store.forecast_sizes(new) if new else {}
        with self._lock:
            if self._writes != before:
                return
            self._forecasts.extend(new)
            self._sizes.update(sizes)
            self._schedules = schedules
            self._stored_read = time.monotonic()
            if new:
                self._table_sizes.clear()
        if new:
            logger.info("Found %d runs written by a job", len(new))

    def _keep_reading_stored(self) -> None:
        while True:
            time.sleep(max(STORED_FRESH_SECONDS, 5))
            try:
                self._read_stored()
            except Exception:
                logger.warning("Could not read the lists of runs again", exc_info=True)

    def _schedule_json(self, schedule: Schedule | None) -> dict[str, Any] | None:
        if schedule is None or schedule.state == RETIRED:
            return None
        return {
            "cron": schedule.cron,
            "next_run": _stamp(next_run(schedule.cron)) if schedule.state == ACTIVE else None,
            "state": schedule.state,
            "job_name": schedule.job_name,
            "jobs_address": self.scheduler.address(schedule.job_id) if self.scheduler else None,
            "since": schedule.created_at.isoformat(),
        }

    def promote(self, dataset: str, name: str, cron: str) -> dict[str, Any]:
        """Put a forecast in production: a job issues it again on the schedule `cron`."""
        if self.scheduler is None:
            raise UserError("Scheduling is not available here.")
        self.dataset(dataset)
        if not self._issues(dataset, name, Kind.FORECAST):
            raise UserError(f"There is no forecast named {name} for {dataset}.")
        try:
            cron = checked_cron(cron)
        except ValueError as error:
            raise UserError(str(error)) from error
        title = job_name(dataset, name)
        with self._lock:
            before = self._schedules.get((dataset, name))
            if before and before.state != RETIRED:
                self._drop_job(before)
            try:
                job_id = self.scheduler.create(
                    title, cron, {DATASET_VARIABLE: dataset, FORECAST_VARIABLE: name}
                )
            except Exception as error:
                logger.exception("Could not create the job %s", title)
                reason = getattr(error, "reason", None) or str(error).splitlines()[0]
                raise UserError(f"Cloudera AI did not create the job: {reason}.") from error
            now = dt.datetime.now(dt.UTC)
            schedule = Schedule(dataset, name, cron, ACTIVE, job_id, title, now, now)
            self.store.save_schedule(schedule)
            self._schedules[(dataset, name)] = schedule
            self._writes += 1
        return self.production(dataset, name)

    def _drop_job(self, schedule: Schedule) -> None:
        try:
            self.scheduler.delete(schedule.job_id)
        except Exception:
            logger.warning("Could not delete the job %s", schedule.job_name, exc_info=True)

    def set_production_state(self, dataset: str, name: str, state: str) -> dict[str, Any]:
        """Pause a forecast in production, start it again, or take it out of production.

        Taking it out deletes the job. The issues it made, and the record of
        its runs, stay.
        """
        if self.scheduler is None:
            raise UserError("Scheduling is not available here.")
        if state not in (ACTIVE, PAUSED, RETIRED):
            raise UserError("A forecast in production is active, paused or retired.")
        with self._lock:
            schedule = self._schedules.get((dataset, name))
            if schedule is None or schedule.state == RETIRED:
                raise UserError(f"{name} is not in production.")
            if state == RETIRED:
                self._drop_job(schedule)
            else:
                self.scheduler.pause(schedule.job_id, state == PAUSED, schedule.cron)
            schedule = schedule.changed(state)
            self.store.save_schedule(schedule)
            self._schedules[(dataset, name)] = schedule
            self._writes += 1
        return self.production(dataset, name)

    def run_scheduled(self, dataset: str, name: str) -> dict[str, Any]:
        """Do one run of a forecast in production, and write down what it did.

        This is what the job calls. It issues the forecast again from the
        history as it stands. When no history has arrived since the last
        issue it makes nothing and says so. It never switches model: if the
        model the forecast was made with is not deployed, the run fails.
        """
        started = time.monotonic()
        run: dict[str, Any] = {
            "dataset": dataset,
            "forecast_name": name,
            "ran_at": dt.datetime.now(dt.UTC),
            "forecast_id": None,
            "history_end_date": None,
            "series": None,
        }
        try:
            issues = self._issues(dataset, name, Kind.FORECAST)
            if not issues:
                raise UserError(f"There is no forecast named {name} for {dataset}.")
            wanted = issues[-1].model_name
            if wanted not in {choice.model_name for choice in self.choices()}:
                raise RuntimeError(
                    f"The model {wanted} is not deployed. A forecast in production keeps its model."
                )
            result = self.reissue(Job(id="scheduled", label=name), dataset, name, None)
            made = self.record(result["forecast_id"])
            run |= {
                "outcome": ISSUED,
                "detail": f"Ran with history to {made.history_end_date}.",
                "forecast_id": made.forecast_id,
                "history_end_date": made.history_end_date,
                "series": result["series"],
            }
        except NoNewDataError:
            last = self._issues(dataset, name, Kind.FORECAST)[-1].history_end_date
            run |= {
                "outcome": NO_NEW_DATA,
                "detail": f"No history after {last} has arrived. Nothing was run.",
                "history_end_date": last,
            }
        except Exception as error:
            logger.exception("The scheduled run of %s failed", name)
            run |= {"outcome": FAILED, "detail": str(error) or type(error).__name__}
        run["seconds"] = round(time.monotonic() - started, 1)
        with self._lock:
            self.store.log_run(run)
        return run

    def track_record(self, dataset: str, name: str) -> dict[str, Any]:
        """Score every issue of a forecast against the actuals that have arrived since.

        This is the forecast's record in real use, not a test on the past:
        each issue was made without knowing what came next. An issue is
        scored on the dates it predicted that now have an actual.
        """
        issues = self._issues(dataset, name, Kind.FORECAST)
        if not issues:
            raise UserError(f"There is no forecast named {name} for {dataset}.")
        latest = issues[-1]
        key = (
            "track|"
            + ",".join(r.forecast_id for r in issues)
            + f"|{self.dates(self.dataset(dataset))[-1]}"
        )
        scores = self._score_cache.get(key)
        if scores is None:
            panel = self._panel_of(latest)
            scores = score_forecasts(panel, [(record, self.values(record)) for record in issues])
            self._score_cache.put(key, scores)
        scored = scores.scored[scores.scored["actual"].notna()]

        def error(part: pd.DataFrame, column: str) -> float | None:
            known = part[part[column].notna()]
            total = known["actual"].abs().sum()
            return float((known[column] - known["actual"]).abs().sum() / total) if total else None

        rows = []
        for record in issues:
            mine = scored[scored["forecast_id"] == record.forecast_id]
            rows.append(
                {
                    "forecast_id": record.forecast_id,
                    "history_end_date": iso(record.history_end_date),
                    "created_at": record.created_at.isoformat(),
                    "horizon": record.horizon,
                    "dates_scored": int(mine["date"].nunique()),
                    "wape": error(mine, "forecast"),
                    "reference_wape": error(mine, REFERENCE),
                }
            )
        by_step = [
            {
                "step": int(step),
                "issues": int(part["forecast_id"].nunique()),
                "wape": error(part, "forecast"),
                "reference_wape": error(part, REFERENCE),
            }
            for step, part in scored.groupby("step")
        ]
        return {
            "target": latest.target_column,
            "issues": rows,
            "scored_issues": sum(1 for row in rows if row["dates_scored"]),
            "total": {
                "wape": error(scored, "forecast"),
                "reference_wape": error(scored, REFERENCE),
            },
            "by_step": by_step,
        }

    def production(self, dataset: str, name: str) -> dict[str, Any]:
        """Return everything about a forecast in production: schedule, runs and track record."""
        self.dataset(dataset)
        self.refresh()
        with self._lock:
            runs = self.store.runs(dataset, name, 50)
        return {
            "available": self.scheduler is not None,
            "schedule": self._schedule_json(self._schedules.get((dataset, name))),
            "runs": [
                {
                    "ran_at": _stamp(run["ran_at"]),
                    "outcome": run["outcome"],
                    "detail": run["detail"],
                    "history_end_date": iso(run.get("history_end_date")),
                    "series": None if pd.isna(run.get("series")) else int(run["series"]),
                    "seconds": number(run.get("seconds")),
                }
                for run in runs
            ],
            "track": self.track_record(dataset, name),
        }

    # Cleaning up: forecasts that are not in production, and the tables they hold

    def _in_production(self, dataset: str, name: str) -> bool:
        schedule = self._schedules.get((dataset, name))
        return schedule is not None and schedule.state != RETIRED

    def cleanup(self) -> dict[str, Any]:
        """List the forecasts that can be deleted, with the age and size of their tables.

        A forecast in production, running or paused, is not listed. It has
        to be taken out of production first.
        """
        self.refresh()
        groups: dict[tuple[str, str], list[ForecastRecord]] = {}
        for record in self._forecasts:
            groups.setdefault((record.dataset, record.forecast_name), []).append(record)
        listed = []
        for (dataset, name), records in groups.items():
            if self._in_production(dataset, name):
                continue
            tables = sorted({forecast_table(dataset, name, record.kind) for record in records})
            sizes = [self._table_size(table) for table in tables]
            runs = [record for record in records if record.kind is Kind.FORECAST]
            listed.append(
                {
                    "dataset": dataset,
                    "forecast_name": name,
                    "tables": [f"{self.store.database}.{table}" for table in tables],
                    "runs": len(runs),
                    "test_runs": len(records) - len(runs),
                    "rows": sum(size["rows"] for size in sizes),
                    "bytes": sum(size["bytes"] for size in sizes),
                    "first_run": min(record.created_at for record in records).isoformat(),
                    "last_run": max(record.created_at for record in records).isoformat(),
                }
            )
        listed.sort(key=lambda row: row["last_run"])
        return {
            "forecasts": listed,
            "tables": sum(len(row["tables"]) for row in listed),
            "bytes": sum(row["bytes"] for row in listed),
            "oldest": listed[0]["last_run"] if listed else None,
            "in_production": sum(
                1 for (dataset, name) in groups if self._in_production(dataset, name)
            ),
        }

    def _table_size(self, table: str) -> dict[str, int]:
        kept = self._table_sizes.get(table)
        if kept is None:
            with self._lock:
                kept = self.store.table_size(table)
            self._table_sizes[table] = kept
        return kept

    def delete_forecast(self, dataset: str, name: str) -> dict[str, Any]:
        """Delete a forecast for good: its tables, all its runs and its test runs.

        This is the one place where the application removes what it stored.
        A forecast in production is refused.
        """
        self.dataset(dataset)
        self.refresh()
        records = [r for r in self._forecasts if r.dataset == dataset and r.forecast_name == name]
        if not records:
            raise UserError(f"There is no forecast named {name} for {dataset}.")
        if self._in_production(dataset, name):
            raise UserError(f"{name} is in production. Take it out of production first.")
        tables = sorted({forecast_table(dataset, name, record.kind) for record in records})
        with self._lock:
            self.store.delete_forecast(dataset, name, tables)
            gone = {record.forecast_id for record in records}
            self._deleted |= gone
            self._writes += 1
            self._forecasts = [r for r in self._forecasts if r.forecast_id not in gone]
            for forecast_id in gone:
                self._sizes.pop(forecast_id, None)
                self._bands.pop(forecast_id, None)
            self._values.clear()
            self._series_tables.clear()
            self._score_cache.clear()
            self._schedules.pop((dataset, name), None)
            for table in tables:
                self._table_sizes.pop(table, None)
        logger.info("Deleted the forecast %s of %s: %s", name, dataset, ", ".join(tables))
        return self.cleanup()

    # Forecasts: lists

    def _issues(self, dataset: str, name: str, kind: Kind) -> list[ForecastRecord]:
        """Return the issues of a forecast, oldest history first, one per end of history."""
        found: dict[dt.date, ForecastRecord] = {}
        for record in sorted(self._forecasts, key=lambda r: r.created_at):
            if record.dataset == dataset and record.forecast_name == name and record.kind is kind:
                found[record.history_end_date] = record
        return [found[day] for day in sorted(found)]

    def forecast_groups(self, dataset: str) -> list[dict[str, Any]]:
        self.refresh()
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
                    "production": self._schedule_json(self._schedules.get((dataset, name))),
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
        self.refresh()
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
            "values_table": f"{self.store.database}.{forecast_table(dataset, name)}",
            "forecasts_table": f"{self.store.database}.forecasts",
        }

    def values(self, record: ForecastRecord) -> pd.DataFrame:
        """Return the stored rows of a forecast, by series and step."""
        cached = self._values.get(record.forecast_id)
        if cached is None:
            with self._lock:
                cached = self.store.forecast_values([record])
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
            self._writes += 1
            self._table_sizes.clear()
            self._sizes[issued.record.forecast_id] = len(issued.rows)
        made_by = issued.record.dimension_columns
        values = issued.values.copy()
        for column in made_by:
            values[column] = (
                values[column].astype("string").astype(object).where(values[column].notna(), None)
            )
        values["date"] = pd.to_datetime(values["date"])
        # The same columns in the same order as the stored table.
        values = values[list(forecast_values_columns(made_by))]
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

    def check_new_forecast(
        self, dataset: str, forecast_name: str, horizon: int, model: str | None = None
    ) -> str:
        """Refuse a new forecast that cannot be made, before any work starts. Return its name."""
        self.dataset(dataset)
        self._chosen(model)
        name = forecast_name.strip()
        if not name:
            raise UserError("Give the forecast a name.")
        if any(r.dataset == dataset and r.forecast_name == name for r in self._forecasts):
            raise UserError(
                f"A forecast named {name} exists for this dataset. Open it to run it again, "
                "or choose another name."
            )
        try:
            table = forecast_table(dataset, name)
        except ValueError as error:
            raise UserError(str(error)) from error
        clash = next(
            (
                r.forecast_name
                for r in self._forecasts
                if r.dataset == dataset and forecast_table(dataset, r.forecast_name) == table
            ),
            None,
        )
        if clash:
            raise UserError(
                f"The name {name} is too close to the forecast {clash}: both would be stored "
                f"in the table {table}. Choose another name."
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
        model: str | None = None,
    ) -> dict[str, Any]:
        dataset = self.dataset(spec.dataset)
        name = self.check_new_forecast(spec.dataset, forecast_name, horizon, model)
        chosen = self._chosen(model)
        job.report("Loading history from the data lake", 0.03)
        panel = self.panel(spec)
        if panel.n_series == 0:
            raise UserError(
                f"No series has the minimum history of {panel.minimum_history} periods. "
                "Widen the dates or the filter."
            )
        steps = self._cutoff_steps(panel, horizon, cutoffs, cutoff_step)
        forecast_share = 0.85 / (1 + len(steps))
        with self._using(chosen):
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
            raise NoNewDataError(
                f"This forecast was already run with history to {ends}. "
                "There is no newer data to run it with."
            )
        with self._using(self._model_of(latest)):
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
        with self._using(self._model_of(latest)):
            count = self._backtest(job, panel, latest, steps, 0.08, 0.98)
        return {"dataset": dataset, "forecast_name": name, "backtests": count}

    # Forecasts: looking at them

    def _row_of(self, panel: Panel, ts_id: str) -> int:
        try:
            return panel.ts_ids.index(ts_id)
        except ValueError:
            raise UserError("That series is not part of this forecast.") from None

    def _band_of_total(
        self,
        record: ForecastRecord,
        panel: Panel,
        rows: np.ndarray,
        history_steps: int,
        part: str = "",
    ) -> tuple[np.ndarray, np.ndarray] | None:
        """Return the low and high of the total as shares of its forecast.

        The low and high of single series cannot be added up: their sum is far
        wider than the range of the total. So the total's own history is
        forecast once, and its range is taken from that.
        """
        key = record.forecast_id + part
        if key in self._bands:
            return self._bands[key]
        if not self._model_lock.acquire(blocking=False):
            return None  # a job is using the model; the range appears once it is done
        try:
            history = np.nansum(panel.history(history_steps)[rows], axis=0)[None, :]
            result = self._model_of(record).forecast(history, record.horizon)
        except Exception:
            logger.warning("Could not forecast the total of %s", record.forecast_id, exc_info=True)
            return None
        finally:
            self._model_lock.release()
        middle = np.where(result.forecast[0] > 0, result.forecast[0], np.nan)
        band = (result.low[0] / middle, result.high[0] / middle)
        if len(self._bands) > 200:
            self._bands.clear()
        self._bands[key] = band
        return band

    def _drilled(
        self, record: ForecastRecord, panel: Panel, drill: Mapping[str, Any]
    ) -> pd.DataFrame:
        """Return the series of a forecast that have the chosen dimension values."""
        chosen = {column: str(value) for column, value in drill.items() if value not in (None, "")}
        unknown = [column for column in chosen if column not in record.dimension_columns]
        if unknown:
            raise UserError(f"This forecast has no dimension named {', '.join(unknown)}.")
        series = panel.series[panel.series[TS_ID].isin(self.values(record)[TS_ID].unique())]
        for column, value in chosen.items():
            series = series[series[column].astype(str) == value]
        return series

    def drill_options(self, forecast_id: str, drill: Mapping[str, Any]) -> dict[str, Any]:
        """Return, for each dimension of a forecast, the values left to choose from.

        The values of one dimension are those that go with what is chosen in
        the others, so every combination offered has series behind it.
        """
        record = self.record(forecast_id)
        panel = self._panel_of(record)
        options = {}
        for column in record.dimension_columns:
            others = {c: v for c, v in drill.items() if c != column}
            found = self._drilled(record, panel, others)[column].dropna().astype(str).unique()
            options[column] = sorted(found)[:MAX_DRILL_VALUES]
        return {
            "dimensions": list(record.dimension_columns),
            "options": options,
            "series": len(self._drilled(record, panel, drill)),
        }

    def chart(
        self, forecast_id: str, ts_id: str | None, drill: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        """Return history, forecast and range: of one series, of the series that have the
        chosen dimension values added together, or of the total of all series."""
        record = self.record(forecast_id)
        values = self.values(record)
        panel = self._panel_of(record)
        part, part_label = "", ""
        if not ts_id and drill and any(v not in (None, "") for v in drill.values()):
            kept = self._drilled(record, panel, drill)
            if kept.empty:
                raise UserError("No series of this forecast has those values.")
            if len(kept) == 1:
                ts_id = str(kept[TS_ID].iloc[0])
            else:
                values = values[values[TS_ID].isin(kept[TS_ID])]
                chosen = {c: str(v) for c, v in drill.items() if v not in (None, "")}
                part = "|" + json.dumps(sorted(chosen.items()))
                part_label = " · ".join(chosen[c] for c in record.dimension_columns if c in chosen)
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
            band = self._band_of_total(record, panel, rows, history_steps, part)
            if band is None:
                low = high = np.full(horizon, np.nan)
                note = "The range of the total is not available right now."
            else:
                low, high = forecast * band[0], forecast * band[1]
            label = f"Total of {len(ids):,} series"
            if part_label:
                label = f"{part_label}: {len(ids):,} series added together"
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
        name = forecast_table(record.dataset, record.forecast_name, record.kind)
        table = f"{self.store.database}.{name}"
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


def _stamp(value: dt.datetime) -> str:
    """Write a time read from storage, which has no zone, as UTC."""
    stamp = value.to_pydatetime() if hasattr(value, "to_pydatetime") else value
    return (stamp if stamp.tzinfo else stamp.replace(tzinfo=dt.UTC)).isoformat()


def _table_problem(table: str, error: Exception) -> str:
    """Say in plain words why a table could not be read."""
    text = str(error)
    if (
        "TABLE_OR_VIEW_NOT_FOUND" in text
        or "cannot be found" in text
        or "not found" in text.lower()
    ):
        hint = "" if "." in table else " Write it as database.table."
        return f"There is no table named {table}.{hint} Check the database and the table name."
    first = text.splitlines()[0] if text else type(error).__name__
    return f"The table {table} could not be read: {first[:200]}"
