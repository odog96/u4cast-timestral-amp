"""The error measures: MAE, RMSE, MAPE and WAPE."""

import math

import numpy as np
import pytest

from u4cast.metrics import error_metrics, error_metrics_by

ACTUAL = np.array([100.0, 200.0, 0.0, 50.0])
FORECAST = np.array([110.0, 180.0, 5.0, 50.0])  # errors 10, 20, 5 and 0


def test_measures_on_known_numbers():
    result = error_metrics(ACTUAL, FORECAST)
    assert result["n"] == 4
    assert result["mae"] == pytest.approx(35 / 4)
    assert result["rmse"] == pytest.approx(math.sqrt((100 + 400 + 25 + 0) / 4))
    # MAPE leaves out the date whose actual is zero: (10% + 10% + 0%) / 3.
    assert result["mape"] == pytest.approx(0.2 / 3)
    # WAPE keeps it: 35 / 350.
    assert result["wape"] == pytest.approx(0.1)


def test_mape_cannot_be_computed_when_every_actual_is_zero_but_mae_can():
    result = error_metrics(np.zeros(3), np.array([1.0, 2.0, 3.0]))
    assert result["mae"] == pytest.approx(2.0)
    assert math.isnan(result["mape"])
    assert math.isnan(result["wape"])


def test_wape_works_when_some_actuals_are_zero():
    result = error_metrics(np.array([0.0, 0.0, 10.0]), np.array([1.0, 1.0, 8.0]))
    assert result["wape"] == pytest.approx(0.4)
    assert result["mape"] == pytest.approx(0.2)


def test_dates_without_an_actual_or_a_forecast_are_not_scored():
    actual = np.array([100.0, np.nan, 100.0, 100.0])
    forecast = np.array([90.0, 50.0, np.nan, 120.0])
    result = error_metrics(actual, forecast)
    assert result["n"] == 2
    assert result["mae"] == pytest.approx(15.0)
    assert result["wape"] == pytest.approx(30 / 200)


def test_nothing_to_score_gives_missing_measures():
    for actual in (np.array([]), np.array([np.nan, np.nan])):
        result = error_metrics(actual, actual)
        assert result["n"] == 0
        assert all(math.isnan(result[name]) for name in ("mae", "rmse", "mape", "wape"))


def test_measures_per_group():
    groups = np.array(["b", "a", "a", "b", "c"])
    actual = np.array([100.0, 10.0, 30.0, 300.0, np.nan])
    forecast = np.array([110.0, 10.0, 20.0, 240.0, 1.0])
    result = error_metrics_by(groups, actual, forecast)
    assert result.index.tolist() == ["a", "b", "c"]
    assert result["n"].tolist() == [2, 2, 0]
    assert result.loc["a", "mae"] == pytest.approx(5.0)
    assert result.loc["a", "wape"] == pytest.approx(10 / 40)
    assert result.loc["b", "mae"] == pytest.approx(35.0)
    assert result.loc["b", "rmse"] == pytest.approx(math.sqrt((100 + 3600) / 2))
    assert result.loc["b", "mape"] == pytest.approx((0.1 + 0.2) / 2)
    assert result.loc["c"].isna().sum() == 4
