"""Turn rows of date, dimensions and target into the array of series the model takes.

The rows are the slice a forecast is made for: already filtered, one row per
date and combination of dimension values, or several that are summed here.
The result is one array with a row per series and a column per date of a
regular grid, with NaN where a series has no data for a date.
"""

from __future__ import annotations

import enum
import logging
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd

from u4cast.frequency import Calendar, Frequency, minimum_history

logger = logging.getLogger(__name__)

TS_ID = "ts_id"
# The ts_id of the single series of a dataset that has no dimension columns.
TOTAL_TS_ID = "total"


class LeftOutReason(enum.StrEnum):
    """Why a series was left out of a forecast."""

    NO_VALUES = "no_values"
    """The series has no value on any date of the history window."""

    UNEQUAL_LENGTH = "unequal_length"
    """The series starts after the window starts, or ends before it ends."""

    TOO_SHORT = "too_short"
    """The history window has fewer steps than the minimum history."""


@dataclass(frozen=True)
class Panel:
    """The series of one slice on one regular date grid.

    `values` has one row per series, in the order of `series`, and one column
    per date in `dates`. A date with no data is NaN, not zero.
    """

    frequency: Frequency
    calendar: Calendar
    dates: pd.DatetimeIndex
    dimension_columns: tuple[str, ...]
    series: pd.DataFrame
    """One row per kept series: `ts_id`, then the dimension columns."""
    values: np.ndarray
    """Shape (series, steps), 64-bit floats, NaN where a date has no data."""
    left_out: pd.DataFrame
    """One row per series left out: `ts_id`, the dimension columns, `reason`,
    `first_date`, `last_date` and `steps` (grid steps from first to last date)."""
    rows_off_grid: int
    """Rows dropped because their date is missing or not on the grid."""
    minimum_history: int

    @property
    def ts_ids(self) -> list[str]:
        return self.series[TS_ID].tolist()

    @property
    def n_series(self) -> int:
        return self.values.shape[0]

    @property
    def n_steps(self) -> int:
        return self.values.shape[1]


def _dimension_text(column: pd.Series) -> pd.Series:
    """Write a dimension column as text that does not depend on how it was loaded.

    A column of whole numbers reads as floats when it has a missing value.
    Writing 2633.0 as "2633" keeps a series' ts_id the same either way.
    """
    if pd.api.types.is_float_dtype(column):
        known = column.dropna()
        if len(known) and bool((known % 1 == 0).all()):
            column = column.astype("Int64")
    text = column.astype("string").fillna("")
    return text.str.replace("\\", "\\\\", regex=False).str.replace("|", "\\|", regex=False)


def make_ts_ids(frame: pd.DataFrame, dimension_columns: Sequence[str]) -> np.ndarray:
    """Return the ts_id of every row, built from its dimension values.

    The ts_id is `column=value` for each dimension column, joined by `|`, for
    example `store=2633|category=VODKA`. It depends only on the values, so a
    series has the same ts_id in every forecast, whatever else is in the data.
    """
    if not dimension_columns:
        return np.full(len(frame), TOTAL_TS_ID, dtype=object)
    if len(frame) == 0:
        return np.empty(0, dtype=object)
    columns = list(dimension_columns)
    keys = frame[columns]
    group = keys.groupby(columns, sort=False, dropna=False).ngroup().to_numpy()
    first_rows = np.unique(group, return_index=True)[1]
    combinations = keys.iloc[first_rows]
    ids = None
    for column in columns:
        part = column + "=" + _dimension_text(combinations[column])
        ids = part if ids is None else ids + "|" + part
    return ids.to_numpy(dtype=object)[group]


def _numbers(column: pd.Series) -> np.ndarray:
    try:
        return pd.to_numeric(column).to_numpy(dtype=np.float64)
    except (ValueError, TypeError) as error:
        raise ValueError(f"The target column {column.name} must hold numbers.") from error


def _require_columns(data: pd.DataFrame, columns: Sequence[str]) -> None:
    missing = [column for column in columns if column not in data.columns]
    if missing:
        raise ValueError(f"The data has no column named {', '.join(missing)}.")
    if len(set(columns)) != len(columns):
        raise ValueError("The date, target and dimension columns must all be different columns.")
    if TS_ID in columns:
        raise ValueError(f"A column cannot be named {TS_ID}; the application uses that name.")


def prepare(
    data: pd.DataFrame,
    *,
    date_column: str,
    target_column: str,
    dimension_columns: Sequence[str],
    frequency: Frequency,
    history_start: object | None = None,
    history_end: object | None = None,
    minimum_history_override: int | None = None,
) -> Panel:
    """Build the array of series for one forecast.

    Each combination of dimension values is one series. Rows of the same
    series and date are summed. `history_start` and `history_end` are the
    first and last date of history to use, both included; by default all of
    it is used. The history window runs from the first to the last date that
    has data within those limits.

    A series is left out, and listed in `Panel.left_out`, when it has no value
    in the window, when it does not cover the whole window, or when the
    window is shorter than the minimum history for the frequency.
    """
    dimension_columns = tuple(dimension_columns)
    _require_columns(data, [date_column, target_column, *dimension_columns])
    lowest_history = minimum_history(frequency, minimum_history_override)

    days = pd.to_datetime(data[date_column])
    if days.dt.tz is not None:
        days = days.dt.tz_localize(None)
    days = days.dt.normalize()
    in_window = days.notna().to_numpy()
    if history_start is not None:
        in_window = in_window & (days >= pd.Timestamp(history_start).normalize()).to_numpy()
    if history_end is not None:
        in_window = in_window & (days <= pd.Timestamp(history_end).normalize()).to_numpy()
    rows_off_grid = int(days.isna().sum())

    rows = pd.DataFrame(
        {
            TS_ID: make_ts_ids(data, dimension_columns),
            "day": days.to_numpy(),
            "value": _numbers(data[target_column]),
        }
    )[in_window]
    dimensions = (
        data.loc[in_window, list(dimension_columns)]
        .assign(**{TS_ID: rows[TS_ID].to_numpy()})
        .drop_duplicates(TS_ID)
        .set_index(TS_ID)
        .sort_index()
    )
    all_ids = dimensions.index

    observed = rows[rows["value"].notna()]
    if len(observed) == 0:
        raise ValueError("The data has no values in the chosen history window.")
    calendar = Calendar.from_dates(frequency, observed["day"])
    steps = calendar.steps_of(observed["day"])
    rows_off_grid += int((steps < 0).sum())
    observed = observed.assign(step=steps)[steps >= 0]
    n_steps = int(observed["step"].max()) + 1
    dates = calendar.dates(0, n_steps)

    cells = observed.groupby([TS_ID, "step"], sort=False)["value"].sum().reset_index()
    spans = cells.groupby(TS_ID)["step"].agg(first="min", last="max").reindex(all_ids)
    has_values = spans["first"].notna()
    covers_window = has_values & (spans["first"] == 0) & (spans["last"] == n_steps - 1)

    reason = pd.Series(LeftOutReason.TOO_SHORT.value, index=all_ids, dtype=object)
    reason[~covers_window] = LeftOutReason.UNEQUAL_LENGTH.value
    reason[~has_values] = LeftOutReason.NO_VALUES.value
    kept = covers_window & (n_steps >= lowest_history)

    kept_ids = all_ids[kept.to_numpy()]
    values = np.full((len(kept_ids), n_steps), np.nan, dtype=np.float64)
    row_of = kept_ids.get_indexer(cells[TS_ID])
    in_kept = row_of >= 0
    values[row_of[in_kept], cells["step"].to_numpy()[in_kept]] = cells["value"].to_numpy()[in_kept]

    left = ~kept.to_numpy()
    first_step = spans["first"][left]
    last_step = spans["last"][left]
    left_out = dimensions[left].reset_index()
    left_out["reason"] = reason[left].to_numpy()
    left_out["first_date"] = _dates_or_missing(calendar, first_step)
    left_out["last_date"] = _dates_or_missing(calendar, last_step)
    left_out["steps"] = (last_step - first_step + 1).fillna(0).astype(int).to_numpy()

    logger.info(
        "Prepared %d series of %d %s steps; left out %d series; dropped %d rows off the grid",
        len(kept_ids),
        n_steps,
        frequency.value,
        int(left.sum()),
        rows_off_grid,
    )
    return Panel(
        frequency=frequency,
        calendar=calendar,
        dates=dates,
        dimension_columns=dimension_columns,
        series=dimensions[kept.to_numpy()].reset_index(),
        values=values,
        left_out=left_out,
        rows_off_grid=rows_off_grid,
        minimum_history=lowest_history,
    )


def _dates_or_missing(calendar: Calendar, steps: pd.Series) -> pd.DatetimeIndex:
    """Return the grid date of each step, and a missing date where the step is missing."""
    known = steps.notna().to_numpy()
    dates = calendar.dates_at(steps.fillna(0).to_numpy(dtype=np.int64))
    return dates.where(known, pd.NaT)
