"""Issue a forecast: call the model once for all series and build the rows to store.

Every forecast gets its own `forecast_id`. Reissuing a forecast on newer data
gives a new `forecast_id` under the same `forecast_name`, so every predicted
value can be traced to the issue that produced it.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from u4cast.layout import ForecastRecord, Kind, forecast_values_columns
from u4cast.model import Forecaster
from u4cast.prepare import Panel

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IssuedForecast:
    """One forecast: its row for the forecasts table and its rows of values."""

    record: ForecastRecord
    values: pd.DataFrame
    """One row per series and future date, in the layout of the forecast values table."""
    rows: np.ndarray
    """Which series of the panel were forecast, as positions in the panel."""


def issue_forecast(
    panel: Panel,
    forecaster: Forecaster,
    *,
    horizon: int,
    forecast_name: str,
    dataset: str,
    target_column: str,
    slice_filter: Mapping[str, Sequence[Any]] | None = None,
    kind: Kind = Kind.FORECAST,
    history_steps: int | None = None,
    created_at: dt.datetime | None = None,
) -> IssuedForecast:
    """Forecast every series of the panel for `horizon` future periods.

    All series go to the model together. `history_steps` uses only that many
    leading steps of the panel as history, which is how a backtest forecasts
    from a past cutoff; by default the whole panel is history. The future
    dates come from the panel's date grid, not from the model.
    """
    columns = list(forecast_values_columns(panel.dimension_columns))
    if panel.n_series == 0:
        raise ValueError("There is no series to forecast: every series was left out.")
    history_steps = panel.n_steps if history_steps is None else history_steps
    if not panel.minimum_history <= history_steps <= panel.n_steps:
        raise ValueError(
            f"The history must have between {panel.minimum_history} and {panel.n_steps} steps; "
            f"got {history_steps}."
        )

    # Series can start at different dates. Forecast the ones that have the
    # minimum history at this end of history; from a past cutoff, fewer do.
    rows = np.flatnonzero(panel.first_steps <= history_steps - panel.minimum_history)
    result = forecaster.forecast(panel.history(history_steps)[rows], horizon)
    expected = (len(rows), horizon)
    for name in ("forecast", "low", "high"):
        if getattr(result, name).shape != expected:
            raise ValueError(
                f"The model returned {name} of shape {getattr(result, name).shape}; "
                f"expected {expected}."
            )

    forecast_id = uuid.uuid4().hex
    record = ForecastRecord(
        forecast_id=forecast_id,
        forecast_name=forecast_name,
        dataset=dataset,
        target_column=target_column,
        dimension_columns=panel.dimension_columns,
        slice_filter=dict(slice_filter or {}),
        frequency=panel.frequency,
        history_start_date=panel.dates[0].date(),
        history_end_date=panel.dates[history_steps - 1].date(),
        horizon=horizon,
        model_name=forecaster.model_name,
        kind=kind,
        created_at=created_at or dt.datetime.now(dt.UTC),
        fill=panel.fill,
        minimum_history=panel.minimum_history,
    )

    future_dates = panel.calendar.future_dates(history_steps, horizon)
    values = panel.series.iloc[np.repeat(rows, horizon)].reset_index(drop=True)
    values["forecast_id"] = forecast_id
    values["date"] = np.tile(future_dates.to_numpy(), len(rows))
    values["forecast"] = result.forecast.ravel()
    values["low"] = result.low.ravel()
    values["high"] = result.high.ravel()
    values["step"] = np.tile(np.arange(1, horizon + 1), len(rows))
    values = values[columns]

    logger.info(
        "Issued %s %s (%s): %d series, history to %s, horizon %d, %d rows",
        kind.value,
        forecast_name,
        forecast_id,
        len(rows),
        record.history_end_date,
        horizon,
        len(values),
    )
    return IssuedForecast(record, values, rows)


def reissue_forecast(
    previous: ForecastRecord,
    panel: Panel,
    forecaster: Forecaster,
    *,
    horizon: int | None = None,
) -> IssuedForecast:
    """Issue a forecast again on newer data.

    The new forecast keeps the name, dataset, target and slice filter of
    `previous` and gets a new `forecast_id`. `panel` is the same slice
    prepared from the data as it is now. The horizon stays the same unless
    one is given.
    """
    if panel.dimension_columns != previous.dimension_columns:
        raise ValueError(
            "A reissue must use the same dimension columns as the forecast it reissues: "
            f"{', '.join(previous.dimension_columns)}."
        )
    if panel.frequency is not previous.frequency:
        raise ValueError(
            f"A reissue must use the same frequency as the forecast it reissues: "
            f"{previous.frequency.value}."
        )
    if panel.fill is not previous.fill:
        raise ValueError(
            "A reissue must fill dates with no row the same way as the forecast it reissues: "
            f"{previous.fill.value}."
        )
    return issue_forecast(
        panel,
        forecaster,
        horizon=previous.horizon if horizon is None else horizon,
        forecast_name=previous.forecast_name,
        dataset=previous.dataset,
        target_column=previous.target_column,
        slice_filter=previous.slice_filter,
    )
