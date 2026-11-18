"""Backtest: forecast from past cutoff dates and compare with what actually happened.

Nothing is retrained. Each cutoff is one forecast whose history ends at the
cutoff, stored like any other forecast with `kind` set to `backtest`. The
reference comparison needs no model: it repeats the value from one season
earlier.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from u4cast.forecast import IssuedForecast, issue_forecast
from u4cast.frequency import SEASON_LENGTH
from u4cast.layout import ForecastRecord, Kind
from u4cast.metrics import METRICS, error_metrics, error_metrics_by
from u4cast.model import Forecaster
from u4cast.prepare import TS_ID, Panel

logger = logging.getLogger(__name__)

REFERENCE = "reference"


@dataclass(frozen=True)
class BacktestResult:
    """What a backtest produced."""

    forecasts: list[IssuedForecast]
    """One forecast per cutoff, `kind` backtest, `history_end_date` the cutoff."""
    scored: pd.DataFrame
    """The forecast values rows of every cutoff, with `actual` and `reference` added."""
    by_series: pd.DataFrame
    """Error per series, worst WAPE first: `ts_id`, the dimension columns, `n`,
    the four measures, and the same four for the reference as `reference_mae`
    and so on."""
    total: dict[str, float]
    """The same measures over all series together."""
    by_step: pd.DataFrame
    """The same measures by how many periods ahead the forecast was: `step`,
    `n`, the four measures and the reference's."""


@dataclass(frozen=True)
class Scores:
    """Forecasts scored against actuals: the parts of a `BacktestResult` that are not forecasts."""

    scored: pd.DataFrame
    by_series: pd.DataFrame
    total: dict[str, float]
    by_step: pd.DataFrame


def backtest_cutoffs(
    panel: Panel, *, first_cutoff: object, step: int, horizon: int
) -> pd.DatetimeIndex:
    """Return the cutoff dates of a backtest.

    A cutoff is the last date of history a forecast may use. Cutoffs start at
    `first_cutoff` and move forward `step` periods at a time, for as long as
    `horizon` dates of actuals follow the cutoff.
    """
    if step < 1:
        raise ValueError(f"The step between cutoffs must be at least 1; got {step}.")
    if horizon < 1:
        raise ValueError(f"The horizon must be at least 1; got {horizon}.")
    first = int(panel.calendar.steps_of([first_cutoff])[0])
    if first < 0 or first >= panel.n_steps:
        raise ValueError(
            f"The first cutoff {pd.Timestamp(first_cutoff).date()} is not a date of the history, "
            f"which runs from {panel.dates[0].date()} to {panel.dates[-1].date()}."
        )
    if first + 1 < panel.minimum_history:
        raise ValueError(
            f"The first cutoff leaves {first + 1} steps of history; the minimum is "
            f"{panel.minimum_history}. The earliest cutoff is "
            f"{panel.dates[panel.minimum_history - 1].date()}."
        )
    last = panel.n_steps - 1 - horizon
    if first > last:
        raise ValueError(
            f"No cutoff has {horizon} dates of actuals after it. "
            + (
                f"The latest first cutoff is {panel.dates[last].date()}."
                if last + 1 >= panel.minimum_history
                else "The history is too short for this horizon."
            )
        )
    return panel.dates[np.arange(first, last + 1, step)]


def seasonal_reference(history: np.ndarray, horizon: int, season: int) -> np.ndarray:
    """Forecast by repeating the value from one season earlier.

    `history` has shape (series, steps). The result has shape (series,
    horizon). A future step further than one season ahead repeats the last
    season again. Where the history is shorter than a season, or the value
    one season earlier is missing, the result is NaN.
    """
    steps = history.shape[1]
    ahead = np.arange(1, horizon + 1)
    source = steps - 1 + ahead - season * np.ceil(ahead / season).astype(int)
    reference = np.full((history.shape[0], horizon), np.nan)
    usable = source >= 0
    reference[:, usable] = history[:, source[usable]]
    return reference


def run_backtest(
    panel: Panel,
    forecaster: Forecaster,
    *,
    first_cutoff: object,
    step: int,
    horizon: int,
    forecast_name: str,
    dataset: str,
    target_column: str,
    slice_filter: Mapping[str, Sequence[Any]] | None = None,
) -> BacktestResult:
    """Forecast from every cutoff and score the forecasts against the actuals.

    The model is called once per cutoff, with all series together. A series
    that starts late is forecast only from the cutoffs by which it has the
    minimum history. Dates with no row are filled from the history up to the
    cutoff alone, so nothing from after a cutoff reaches the model.
    """
    cutoffs = backtest_cutoffs(panel, first_cutoff=first_cutoff, step=step, horizon=horizon)
    forecasts = [
        issue_forecast(
            panel,
            forecaster,
            horizon=horizon,
            forecast_name=forecast_name,
            dataset=dataset,
            target_column=target_column,
            slice_filter=slice_filter,
            kind=Kind.BACKTEST,
            history_steps=int(history_steps),
        )
        for history_steps in panel.calendar.steps_of(cutoffs) + 1
    ]
    scores = score_forecasts(panel, [(issued.record, issued.values) for issued in forecasts])
    logger.info(
        "Backtest %s: %d cutoffs from %s to %s, %d series, WAPE %.3f, reference WAPE %.3f",
        forecast_name,
        len(cutoffs),
        cutoffs[0].date(),
        cutoffs[-1].date(),
        panel.n_series,
        scores.total["wape"],
        scores.total[f"{REFERENCE}_wape"],
    )
    return BacktestResult(forecasts, scores.scored, scores.by_series, scores.total, scores.by_step)


def score_forecasts(
    panel: Panel, forecasts: Sequence[tuple[ForecastRecord, pd.DataFrame]]
) -> Scores:
    """Score forecasts against what actually happened, and against the reference.

    Each forecast is its row of the forecasts table and its rows of values,
    as issued or as read back from storage. `panel` is the same slice with
    history running past the forecasts' dates. A forecast date the panel has
    no actual for is not scored.
    """
    season = SEASON_LENGTH[panel.frequency]
    position = pd.Index(panel.ts_ids)
    parts: list[pd.DataFrame] = []
    for record, values in forecasts:
        history_steps = int(panel.calendar.steps_of([record.history_end_date])[0]) + 1
        if history_steps < 1:
            raise ValueError(
                f"The forecast {record.forecast_id} ends its history on "
                f"{record.history_end_date}, which is not a date of this history."
            )
        row = position.get_indexer(values[TS_ID])
        step = panel.calendar.steps_of(values["date"])
        ahead = values["step"].to_numpy(dtype=np.int64) - 1
        known = (row >= 0) & (step >= 0) & (step < panel.n_steps)
        actual = np.full(len(values), np.nan)
        actual[known] = panel.actuals[row[known], step[known]]
        reference = np.full(len(values), np.nan)
        in_panel = row >= 0
        repeated = seasonal_reference(panel.history(history_steps), record.horizon, season)
        reference[in_panel] = repeated[row[in_panel], ahead[in_panel]]
        parts.append(values.assign(actual=actual, **{REFERENCE: reference}))
    scored = pd.concat(parts, ignore_index=True)

    model_error = error_metrics_by(scored[TS_ID], scored["actual"], scored["forecast"])
    reference_error = error_metrics_by(scored[TS_ID], scored["actual"], scored[REFERENCE])
    by_series = (
        panel.series.join(model_error, on=TS_ID)
        .fillna({"n": 0})
        .astype({"n": int})
        .join(reference_error[list(METRICS)].add_prefix(f"{REFERENCE}_"), on=TS_ID)
        .sort_values(["wape", TS_ID], ascending=[False, True], na_position="last")
        .reset_index(drop=True)
    )
    by_step = error_metrics_by(scored["step"], scored["actual"], scored["forecast"]).join(
        error_metrics_by(scored["step"], scored["actual"], scored[REFERENCE])[
            list(METRICS)
        ].add_prefix(f"{REFERENCE}_")
    )
    total = error_metrics(scored["actual"], scored["forecast"])
    reference_total = error_metrics(scored["actual"], scored[REFERENCE])
    total.update({f"{REFERENCE}_{name}": reference_total[name] for name in METRICS})
    return Scores(scored, by_series, total, by_step.reset_index(names="step"))
