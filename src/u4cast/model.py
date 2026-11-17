"""The one interface the forecasting code calls the model through.

`Forecaster.forecast` takes histories and a horizon and returns forecasts.
Nothing else in the code knows where the model runs. This module holds the
interface, the implementation that loads Timestral in the same process, and a
stand-in that needs no weights. The implementation that calls a Cloudera AI
Workbench model endpoint is in `u4cast.endpoint`, and `u4cast.serving` picks
between them from the settings.
"""

from __future__ import annotations

import abc
import logging
from dataclasses import dataclass
from typing import Any, Protocol

import numpy as np

from u4cast.settings import DEFAULT_BATCH_SIZE, DEFAULT_MODEL_NAME

logger = logging.getLogger(__name__)

# The quantiles kept as the low and high of the forecast range.
LOW_LEVEL = 0.1
HIGH_LEVEL = 0.9


@dataclass(frozen=True)
class ModelForecast:
    """Forecasts for a set of series. Each array has shape (series, horizon)."""

    forecast: np.ndarray
    """The median."""
    low: np.ndarray
    high: np.ndarray
    low_level: float
    """The quantile level `low` holds, as reported by the model."""
    high_level: float


class Forecaster(Protocol):
    """Anything that can forecast a set of series."""

    model_name: str

    def forecast(self, histories: np.ndarray, horizon: int) -> ModelForecast:
        """Forecast `horizon` steps after each history.

        `histories` has shape (series, steps); NaN marks a missing value. All
        series go in together: the caller never loops one call per series.
        """
        ...


def _check_request(histories: np.ndarray, horizon: int) -> np.ndarray:
    histories = np.asarray(histories, dtype=np.float64)
    if histories.ndim != 2:
        raise ValueError(
            f"Histories must have shape (series, steps); got {histories.ndim} dimensions."
        )
    if histories.shape[0] < 1 or histories.shape[1] < 1:
        raise ValueError(f"There is nothing to forecast: histories have shape {histories.shape}.")
    if horizon < 1:
        raise ValueError(f"The horizon must be at least 1; got {horizon}.")
    return histories


class BatchedForecaster(abc.ABC):
    """A forecaster that sends series to the model in batches of `batch_size`.

    Batches keep each model call within memory and, for an endpoint, within
    its limits on request size and time. A series' forecast does not depend
    on which batch it is in.
    """

    model_name: str
    batch_size: int

    def forecast(self, histories: np.ndarray, horizon: int) -> ModelForecast:
        histories = _check_request(histories, horizon)
        size = self._batch_size_for(histories.shape[1], horizon)
        parts = [
            self._forecast_batch(histories[start : start + size], horizon)
            for start in range(0, histories.shape[0], size)
        ]
        logger.info(
            "Forecast %d series of %d steps, horizon %d, in %d model calls",
            histories.shape[0],
            histories.shape[1],
            horizon,
            len(parts),
        )
        return ModelForecast(
            forecast=np.concatenate([part.forecast for part in parts]),
            low=np.concatenate([part.low for part in parts]),
            high=np.concatenate([part.high for part in parts]),
            low_level=parts[0].low_level,
            high_level=parts[0].high_level,
        )

    def _batch_size_for(self, steps: int, horizon: int) -> int:
        """Return how many series go in one model call for this history length and horizon."""
        return self.batch_size

    @abc.abstractmethod
    def _forecast_batch(self, histories: np.ndarray, horizon: int) -> ModelForecast:
        """Forecast one batch. `histories` is already checked and has at most `batch_size` rows."""


def nearest_level(levels: tuple[float, ...], wanted: float) -> float:
    """Return the quantile level the model offers that is closest to `wanted`."""
    if not levels:
        raise ValueError("The model reported no quantile levels.")
    return min(levels, key=lambda level: abs(level - wanted))


class InProcessForecaster(BatchedForecaster):
    """Timestral loaded in this process. Runs on CPU.

    The weights are downloaded from Hugging Face on first use and cached. If
    the environment variable `HF_TOKEN` is set, the download uses it; it is
    not needed. Needs the `model` extra of this package.
    """

    def __init__(
        self, model_name: str = DEFAULT_MODEL_NAME, batch_size: int = DEFAULT_BATCH_SIZE
    ) -> None:
        if batch_size < 1:
            raise ValueError(f"The batch size must be at least 1; got {batch_size}.")
        self.model_name = model_name
        self.batch_size = batch_size
        self._model: Any = None

    def load(self) -> None:
        """Load the model now, so that the first forecast does not wait for it."""
        self._load()

    def _load(self) -> Any:
        if self._model is None:
            from timestral import TimestralForecaster

            logger.info("Loading %s", self.model_name)
            self._model = TimestralForecaster.from_pretrained(self.model_name)
        return self._model

    def _forecast_batch(self, histories: np.ndarray, horizon: int) -> ModelForecast:
        # Two dimensions on purpose: Timestral forecasts the variates of a
        # three-dimensional input jointly, which changes the numbers.
        result = self._load().forecast(histories.astype(np.float32), prediction_length=horizon)
        levels = tuple(float(level) for level in result.quantile_levels)
        low_level = nearest_level(levels, LOW_LEVEL)
        high_level = nearest_level(levels, HIGH_LEVEL)
        return ModelForecast(
            forecast=_series_by_horizon(result.median),
            low=_series_by_horizon(result.quantile(low_level)),
            high=_series_by_horizon(result.quantile(high_level)),
            low_level=low_level,
            high_level=high_level,
        )


def _series_by_horizon(values: Any) -> np.ndarray:
    """Turn Timestral's (series, 1, horizon) output into a (series, horizon) array."""
    array = np.asarray(values, dtype=np.float64)
    if array.ndim != 3 or array.shape[1] != 1:
        raise ValueError(f"Expected model output of shape (series, 1, horizon); got {array.shape}.")
    return array[:, 0, :]


class StandInForecaster(BatchedForecaster):
    """A stand-in for Timestral that needs no weights and returns fixed numbers.

    Every future step of a series gets that series' last known value. Low is
    20% below it and high 20% above. A series with no known value gets zero.
    `calls` records the shape of every batch, so tests can check how the
    model was called.
    """

    def __init__(self, batch_size: int = DEFAULT_BATCH_SIZE) -> None:
        self.model_name = "stand-in"
        self.batch_size = batch_size
        self.calls: list[tuple[int, int, int]] = []

    def _forecast_batch(self, histories: np.ndarray, horizon: int) -> ModelForecast:
        self.calls.append((histories.shape[0], histories.shape[1], horizon))
        known = ~np.isnan(histories)
        last_known = histories.shape[1] - 1 - np.argmax(known[:, ::-1], axis=1)
        last_value = np.where(
            known.any(axis=1), histories[np.arange(histories.shape[0]), last_known], 0.0
        )
        forecast = np.repeat(last_value[:, None], horizon, axis=1)
        return ModelForecast(
            forecast=forecast,
            low=forecast - 0.2 * np.abs(forecast),
            high=forecast + 0.2 * np.abs(forecast),
            low_level=LOW_LEVEL,
            high_level=HIGH_LEVEL,
        )
