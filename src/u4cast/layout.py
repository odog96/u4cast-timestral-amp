"""The layout of the stored tables: forecast values, forecasts and datasets.

This module names the columns and builds the rows. Reading and writing the
Iceberg tables is the storage layer's job. History and forecasts are in
separate tables, and nothing here writes to a history table.
"""

from __future__ import annotations

import datetime as dt
import enum
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from u4cast.frequency import Frequency
from u4cast.prepare import TS_ID, Fill


class Kind(enum.StrEnum):
    """What a stored forecast is."""

    FORECAST = "forecast"
    BACKTEST = "backtest"


# Each table is a list of (column name, Spark SQL type).
FORECASTS_TABLE: tuple[tuple[str, str], ...] = (
    ("forecast_id", "string"),
    ("forecast_name", "string"),
    ("dataset", "string"),
    ("target_column", "string"),
    ("dimension_columns", "string"),  # a JSON list of column names
    ("slice_filter", "string"),  # a JSON object: dimension column to the values kept
    ("frequency", "string"),
    ("history_start_date", "date"),
    ("history_end_date", "date"),
    ("horizon", "int"),
    ("model_name", "string"),
    ("kind", "string"),
    ("created_at", "timestamp"),
    ("fill", "string"),  # what a date with no row became: see u4cast.prepare.Fill
    ("minimum_history", "int"),  # the minimum history a series needed, in steps
)

DATASETS_TABLE: tuple[tuple[str, str], ...] = (
    ("dataset", "string"),
    ("source_table", "string"),
    ("date_column", "string"),
    ("target_column", "string"),
    ("dimension_columns", "string"),  # a JSON list of column names
    ("frequency", "string"),
    ("fill", "string"),  # what a date with no row means: see u4cast.prepare.Fill
)

# The forecast values table has the dataset's dimension columns between
# these two groups, so each dataset has its own table.
_VALUES_BEFORE_DIMENSIONS: tuple[tuple[str, str], ...] = (
    ("forecast_id", "string"),
    (TS_ID, "string"),
)
_VALUES_AFTER_DIMENSIONS: tuple[tuple[str, str], ...] = (
    ("date", "date"),
    ("forecast", "double"),
    ("low", "double"),
    ("high", "double"),
    ("step", "int"),
)


def forecast_values_columns(dimension_columns: Sequence[str]) -> tuple[str, ...]:
    """Return the column names of a dataset's forecast values table, in order."""
    fixed = {name for name, _ in _VALUES_BEFORE_DIMENSIONS + _VALUES_AFTER_DIMENSIONS}
    clashes = [column for column in dimension_columns if column in fixed]
    if clashes:
        raise ValueError(
            f"A dimension column cannot be named {', '.join(clashes)}; "
            "the forecast values table uses that name."
        )
    return (
        *(name for name, _ in _VALUES_BEFORE_DIMENSIONS),
        *dimension_columns,
        *(name for name, _ in _VALUES_AFTER_DIMENSIONS),
    )


def values_table_schema(dimension_columns: Sequence[str]) -> list[tuple[str, str]]:
    """Return the columns of a forecast values table as (name, Spark SQL type).

    Dimension columns are stored as text, whatever their type in the history.
    """
    forecast_values_columns(dimension_columns)  # refuses a clash of names
    return [
        *_VALUES_BEFORE_DIMENSIONS,
        *((column, "string") for column in dimension_columns),
        *_VALUES_AFTER_DIMENSIONS,
    ]


def forecast_values_table(dataset: str) -> str:
    """Return the name of the forecast values table of a dataset."""
    slug = re.sub(r"[^a-z0-9]+", "_", dataset.lower()).strip("_")
    if not slug:
        raise ValueError(f"The dataset name {dataset!r} has no letter or digit to name a table by.")
    return f"forecast_values_{slug}"


def _fill_of(row: Mapping[str, Any]) -> Fill:
    """Read `fill` from a stored row. A row stored before the column existed has none."""
    value = row.get("fill")
    return Fill(value) if isinstance(value, str) and value else Fill.MISSING


def _json_list(values: Sequence[str]) -> str:
    return json.dumps(list(values))


def _json_filter(slice_filter: Mapping[str, Sequence[Any]]) -> str:
    return json.dumps(
        {column: list(values) for column, values in slice_filter.items()},
        sort_keys=True,
        default=str,
    )


@dataclass(frozen=True)
class ForecastRecord:
    """One row of the forecasts table: everything that says what a forecast is.

    A reissue of a forecast is a new record with a new `forecast_id` under the
    same `forecast_name` and the same `slice_filter`. `forecast_id` and
    `history_end_date` tell the issues apart.
    """

    forecast_id: str
    forecast_name: str
    dataset: str
    target_column: str
    dimension_columns: tuple[str, ...]
    frequency: Frequency
    history_start_date: dt.date
    history_end_date: dt.date
    horizon: int
    model_name: str
    kind: Kind
    created_at: dt.datetime
    slice_filter: Mapping[str, Sequence[Any]] = field(default_factory=dict)
    """Dimension column to the values kept. Empty means the whole dataset."""
    fill: Fill = Fill.MISSING
    """What a date with no row in the history became."""
    minimum_history: int = 0
    """The minimum history a series needed, in steps. A reissue uses the same.
    Zero in a row stored before this was recorded."""

    def to_row(self) -> dict[str, Any]:
        """Return the record as a row of the forecasts table."""
        row = {
            "forecast_id": self.forecast_id,
            "forecast_name": self.forecast_name,
            "dataset": self.dataset,
            "target_column": self.target_column,
            "dimension_columns": _json_list(self.dimension_columns),
            "slice_filter": _json_filter(self.slice_filter),
            "frequency": self.frequency.value,
            "history_start_date": self.history_start_date,
            "history_end_date": self.history_end_date,
            "horizon": self.horizon,
            "model_name": self.model_name,
            "kind": self.kind.value,
            "created_at": self.created_at,
            "fill": self.fill.value,
            "minimum_history": self.minimum_history,
        }
        return {name: row[name] for name, _ in FORECASTS_TABLE}

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> ForecastRecord:
        """Build the record from a row of the forecasts table."""
        return cls(
            forecast_id=row["forecast_id"],
            forecast_name=row["forecast_name"],
            dataset=row["dataset"],
            target_column=row["target_column"],
            dimension_columns=tuple(json.loads(row["dimension_columns"])),
            slice_filter=json.loads(row["slice_filter"]),
            frequency=Frequency(row["frequency"]),
            history_start_date=pd.Timestamp(row["history_start_date"]).date(),
            history_end_date=pd.Timestamp(row["history_end_date"]).date(),
            horizon=int(row["horizon"]),
            model_name=row["model_name"],
            kind=Kind(row["kind"]),
            created_at=pd.Timestamp(row["created_at"]).to_pydatetime(),
            fill=_fill_of(row),
            minimum_history=int(row["minimum_history"])
            if pd.notna(row.get("minimum_history"))
            else 0,
        )


@dataclass(frozen=True)
class DatasetRecord:
    """One row of the datasets table: a saved dataset and the role of its columns."""

    dataset: str
    source_table: str
    date_column: str
    target_column: str
    dimension_columns: tuple[str, ...]
    frequency: Frequency
    fill: Fill = Fill.MISSING
    """What a date with no row means in this dataset. The user says so when
    adding the dataset."""

    def to_row(self) -> dict[str, Any]:
        """Return the record as a row of the datasets table."""
        row = {
            "dataset": self.dataset,
            "source_table": self.source_table,
            "date_column": self.date_column,
            "target_column": self.target_column,
            "dimension_columns": _json_list(self.dimension_columns),
            "frequency": self.frequency.value,
            "fill": self.fill.value,
        }
        return {name: row[name] for name, _ in DATASETS_TABLE}

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> DatasetRecord:
        """Build the record from a row of the datasets table."""
        return cls(
            dataset=row["dataset"],
            source_table=row["source_table"],
            date_column=row["date_column"],
            target_column=row["target_column"],
            dimension_columns=tuple(json.loads(row["dimension_columns"])),
            frequency=Frequency(row["frequency"]),
            fill=_fill_of(row),
        )
