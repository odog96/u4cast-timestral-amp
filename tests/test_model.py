"""The model interface: batching, the stand-in, and the in-process Timestral wrapper."""

import sys
import types
from typing import ClassVar

import numpy as np
import pytest

from u4cast.model import InProcessForecaster, StandInForecaster, nearest_level
from u4cast.settings import Settings


def histories(n_series: int, n_steps: int = 40) -> np.ndarray:
    return np.arange(n_series * n_steps, dtype=np.float64).reshape(n_series, n_steps)


def test_stand_in_returns_fixed_numbers():
    data = np.array([[1.0, 2.0, 10.0], [5.0, 20.0, np.nan], [np.nan, np.nan, np.nan]])
    result = StandInForecaster().forecast(data, horizon=2)
    assert result.forecast.tolist() == [[10.0, 10.0], [20.0, 20.0], [0.0, 0.0]]
    assert result.low.tolist() == [[8.0, 8.0], [16.0, 16.0], [0.0, 0.0]]
    assert result.high.tolist() == [[12.0, 12.0], [24.0, 24.0], [0.0, 0.0]]
    assert (result.low_level, result.high_level) == (0.1, 0.9)


def test_all_series_go_to_the_model_in_one_call_when_they_fit_one_batch():
    model = StandInForecaster(batch_size=500)
    model.forecast(histories(100, 32), horizon=12)
    assert model.calls == [(100, 32, 12)]


def test_series_are_sent_in_batches_never_one_call_per_series():
    model = StandInForecaster(batch_size=300)
    result = model.forecast(histories(700), horizon=5)
    assert model.calls == [(300, 40, 5), (300, 40, 5), (100, 40, 5)]
    assert result.forecast.shape == (700, 5)


def test_batch_size_does_not_change_the_result_or_its_order():
    data = histories(11)
    whole = StandInForecaster(batch_size=500).forecast(data, horizon=3)
    split = StandInForecaster(batch_size=4).forecast(data, horizon=3)
    for name in ("forecast", "low", "high"):
        assert np.array_equal(getattr(whole, name), getattr(split, name))


@pytest.mark.parametrize(
    ("data", "horizon", "message"),
    [
        (np.zeros(40), 12, "shape"),
        (np.zeros((2, 3, 40)), 12, "shape"),
        (np.zeros((0, 40)), 12, "nothing to forecast"),
        (np.zeros((3, 40)), 0, "horizon"),
    ],
)
def test_requests_the_model_cannot_answer_are_refused(data, horizon, message):
    with pytest.raises(ValueError, match=message):
        StandInForecaster().forecast(data, horizon)


def test_nearest_level_reads_the_levels_the_model_offers():
    timestral_levels = (0.01, *(round(0.05 * i, 2) for i in range(1, 20)), 0.99)
    assert nearest_level(timestral_levels, 0.1) == 0.1
    assert nearest_level(timestral_levels, 0.9) == 0.9
    assert nearest_level((0.05, 0.25, 0.5, 0.75, 0.95), 0.1) == 0.05
    with pytest.raises(ValueError, match="no quantile levels"):
        nearest_level((), 0.1)


class FakeTimestralForecast:
    """What Timestral's `forecast` returns, with other quantile levels than the real model."""

    quantile_levels = (0.05, 0.25, 0.5, 0.75, 0.95)

    def __init__(self, context: np.ndarray, prediction_length: int) -> None:
        last = context[:, -1][:, None, None]
        self._base = np.repeat(last, prediction_length, axis=2)

    @property
    def median(self) -> np.ndarray:
        return self._base

    def quantile(self, level: float) -> np.ndarray:
        return self._base * (1 + level)


class FakeTimestral:
    loaded: ClassVar[list[str]] = []
    calls: ClassVar[list[tuple[tuple[int, ...], np.dtype, int]]] = []

    @classmethod
    def from_pretrained(cls, name: str) -> "FakeTimestral":
        cls.loaded.append(name)
        return cls()

    def forecast(self, context: np.ndarray, prediction_length: int) -> FakeTimestralForecast:
        self.calls.append((context.shape, context.dtype, prediction_length))
        return FakeTimestralForecast(context, prediction_length)


@pytest.fixture
def fake_timestral(monkeypatch):
    FakeTimestral.loaded = []
    FakeTimestral.calls = []
    module = types.ModuleType("timestral")
    module.TimestralForecaster = FakeTimestral
    monkeypatch.setitem(sys.modules, "timestral", module)
    return FakeTimestral


def test_in_process_forecaster_passes_series_as_a_two_dimensional_array(fake_timestral):
    model = InProcessForecaster("mistralai/Timestral-5M", batch_size=4)
    result = model.forecast(histories(10, 32), horizon=6)
    assert fake_timestral.loaded == ["mistralai/Timestral-5M"]
    assert [call[0] for call in fake_timestral.calls] == [(4, 32), (4, 32), (2, 32)]
    assert {call[1] for call in fake_timestral.calls} == {np.dtype("float32")}
    assert {call[2] for call in fake_timestral.calls} == {6}
    assert result.forecast.shape == (10, 6)
    assert model.model_name == "mistralai/Timestral-5M"


def test_in_process_forecaster_uses_the_levels_the_model_reports(fake_timestral):
    result = InProcessForecaster().forecast(histories(2, 32), horizon=3)
    assert (result.low_level, result.high_level) == (0.05, 0.95)
    last = histories(2, 32)[:, -1]
    assert np.allclose(result.forecast[:, 0], last)
    assert np.allclose(result.low[:, 0], last * 1.05)
    assert np.allclose(result.high[:, 0], last * 1.95)


def test_model_name_and_batch_size_come_from_the_environment():
    defaults = Settings.from_environment({})
    assert (defaults.model_name, defaults.batch_size) == ("mistralai/Timestral-209M", 500)
    changed = Settings.from_environment(
        {"U4CAST_MODEL_NAME": "mistralai/Timestral-28M", "U4CAST_BATCH_SIZE": "100"}
    )
    assert (changed.model_name, changed.batch_size) == ("mistralai/Timestral-28M", 100)
    with pytest.raises(ValueError, match="at least 1"):
        Settings.from_environment({"U4CAST_BATCH_SIZE": "0"})
