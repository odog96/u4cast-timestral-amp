"""Backtests: cutoffs, the reference comparison, scoring and how backtests are stored."""

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from conftest import long_frame, prepare_units
from u4cast.backtest import backtest_cutoffs, run_backtest, seasonal_reference
from u4cast.frequency import Frequency
from u4cast.layout import Kind
from u4cast.model import StandInForecaster
from u4cast.prepare import Fill


def days(index: pd.DatetimeIndex) -> list[str]:
    return list(index.strftime("%Y-%m-%d"))


def backtest(panel, model=None, **options):
    options = {"first_cutoff": "2025-01-01", "step": 3, "horizon": 6} | options
    return run_backtest(
        panel,
        model or StandInForecaster(),
        forecast_name="Monthly demand",
        dataset="liquor",
        target_column="units",
        **options,
    )


def test_cutoffs_start_at_the_first_and_move_by_the_step(monthly_panel):
    # History runs January 2023 to December 2025.
    cutoffs = backtest_cutoffs(monthly_panel, first_cutoff="2025-01-01", step=3, horizon=6)
    assert days(cutoffs) == ["2025-01-01", "2025-04-01"]


def test_the_last_cutoff_leaves_a_full_horizon_of_actuals(monthly_panel):
    cutoffs = backtest_cutoffs(monthly_panel, first_cutoff="2025-01-01", step=1, horizon=6)
    assert days(cutoffs)[-1] == "2025-06-01"
    assert len(cutoffs) == 6
    one = backtest_cutoffs(monthly_panel, first_cutoff="2025-06-01", step=12, horizon=6)
    assert days(one) == ["2025-06-01"]


def test_the_first_cutoff_must_leave_the_minimum_history(monthly_panel):
    earliest = backtest_cutoffs(monthly_panel, first_cutoff="2024-12-01", step=6, horizon=3)
    assert days(earliest)[0] == "2024-12-01"
    with pytest.raises(ValueError, match="earliest cutoff is 2024-12-01"):
        backtest_cutoffs(monthly_panel, first_cutoff="2024-11-01", step=6, horizon=3)


def test_cutoffs_that_cannot_work_are_refused(monthly_panel):
    with pytest.raises(ValueError, match="latest first cutoff is 2025-06-01"):
        backtest_cutoffs(monthly_panel, first_cutoff="2025-07-01", step=1, horizon=6)
    with pytest.raises(ValueError, match="too short for this horizon"):
        backtest_cutoffs(monthly_panel, first_cutoff="2025-07-01", step=1, horizon=20)
    with pytest.raises(ValueError, match="not a date of the history"):
        backtest_cutoffs(monthly_panel, first_cutoff="2025-01-15", step=1, horizon=6)
    with pytest.raises(ValueError, match="not a date of the history"):
        backtest_cutoffs(monthly_panel, first_cutoff="2026-02-01", step=1, horizon=6)
    with pytest.raises(ValueError, match="step"):
        backtest_cutoffs(monthly_panel, first_cutoff="2025-01-01", step=0, horizon=6)
    with pytest.raises(ValueError, match="horizon"):
        backtest_cutoffs(monthly_panel, first_cutoff="2025-01-01", step=1, horizon=0)


def test_reference_repeats_the_value_from_one_season_earlier():
    history = np.arange(1.0, 11.0)[None, :]  # 1 to 10, season of 4: last season is 7, 8, 9, 10
    reference = seasonal_reference(history, horizon=6, season=4)
    assert reference.tolist() == [[7.0, 8.0, 9.0, 10.0, 7.0, 8.0]]


def test_reference_is_missing_where_there_is_no_value_one_season_earlier():
    # Three steps of history, season of 4: the first future step has no value
    # one season earlier; the second one does, the first step of history.
    short = np.array([[1.0, 2.0, 3.0]])
    reference = seasonal_reference(short, horizon=2, season=4)
    assert np.isnan(reference[0, 0])
    assert reference[0, 1] == 1.0
    holed = np.array([[1.0, np.nan, 3.0, 4.0]])
    reference = seasonal_reference(holed, horizon=4, season=4)
    assert reference[0, [0, 2, 3]].tolist() == [1.0, 3.0, 4.0]
    assert np.isnan(reference[0, 1])


def test_backtests_are_stored_as_forecasts_of_kind_backtest(monthly_panel):
    result = backtest(monthly_panel)
    assert [issued.record.kind for issued in result.forecasts] == [Kind.BACKTEST] * 2
    assert [issued.record.history_end_date for issued in result.forecasts] == [
        dt.date(2025, 1, 1),
        dt.date(2025, 4, 1),
    ]
    assert len({issued.record.forecast_id for issued in result.forecasts}) == 2
    assert {issued.record.forecast_name for issued in result.forecasts} == {"Monthly demand"}
    for issued in result.forecasts:
        assert len(issued.values) == 3 * 6
        assert issued.values["date"].min() > pd.Timestamp(issued.record.history_end_date)


def test_the_model_is_called_once_per_cutoff_and_sees_nothing_after_it(monthly_panel):
    model = StandInForecaster()
    result = backtest(monthly_panel, model)
    # History to January 2025 is 25 steps, to April 2025 is 28.
    assert model.calls == [(3, 25, 6), (3, 28, 6)]
    first = result.forecasts[0].values
    # The stand-in repeats the last value it was given: 100 + 24 for SKU A.
    assert set(first.loc[first["ts_id"] == "sku=A", "forecast"]) == {124.0}


def test_forecasts_are_scored_against_what_actually_happened(monthly_panel):
    result = backtest(monthly_panel)
    scored = result.scored
    assert len(scored) == 2 * 3 * 6
    row = scored[(scored["ts_id"] == "sku=B") & (scored["date"] == pd.Timestamp("2025-03-01"))]
    assert row["actual"].tolist() == [226.0]  # 200 + step 26
    assert row["forecast"].tolist() == [224.0]  # the value at the January 2025 cutoff
    assert row["reference"].tolist() == [214.0]  # March 2024, one season earlier
    assert row["step"].tolist() == [2]


def test_error_is_reported_per_series_and_in_total(monthly_panel):
    result = backtest(monthly_panel)
    # Every series rises by 1 a month, so a flat forecast is off by 1 to 6: MAE 3.5.
    by_series = result.by_series.set_index("ts_id")
    assert by_series["n"].tolist() == [12, 12, 12]
    assert by_series["mae"].tolist() == pytest.approx([3.5, 3.5, 3.5])
    assert by_series["rmse"].tolist() == pytest.approx([np.sqrt(91 / 6)] * 3)
    assert by_series["reference_mae"].tolist() == pytest.approx([12.0, 12.0, 12.0])
    assert by_series.loc["sku=A", "sku"] == "A"
    assert result.total["n"] == 36
    assert result.total["mae"] == pytest.approx(3.5)
    assert result.total["reference_mae"] == pytest.approx(12.0)
    actuals = result.scored["actual"].sum()
    assert result.total["wape"] == pytest.approx(3.5 * 36 / actuals)


def test_series_are_listed_worst_first(monthly_panel):
    result = backtest(monthly_panel)
    # The same absolute error weighs most on the smallest series.
    assert result.by_series["ts_id"].tolist() == ["sku=A", "sku=B", "sku=C"]
    assert result.by_series["wape"].is_monotonic_decreasing


def test_dates_without_an_actual_are_not_scored():
    dates = pd.date_range("2023-01-01", periods=36, freq="MS")
    frame = long_frame(dates, ["A", "B"])
    frame = frame[~((frame["sku"] == "A") & (frame["date"] == dates[27]))]
    result = backtest(prepare_units(frame, Frequency.MONTHLY))
    by_series = result.by_series.set_index("ts_id")
    # March 2025 is forecast from the January cutoff only, so one date is not scored.
    assert by_series.loc["sku=A", "n"] == 11
    assert by_series.loc["sku=B", "n"] == 12


def test_weekly_history_under_one_season_has_no_reference():
    weeks = pd.date_range("2024-01-01", periods=45, freq="W-MON")
    panel = prepare_units(long_frame(weeks, ["A"]), Frequency.WEEKLY, minimum_history_override=32)
    result = backtest(panel, first_cutoff=weeks[35], step=4, horizon=4)
    assert result.total["mae"] == pytest.approx(2.5)
    assert np.isnan(result.total["reference_mae"])
    assert result.scored["reference"].isna().all()


def test_a_series_that_starts_late_is_forecast_only_once_it_has_the_minimum_history():
    dates = pd.date_range("2023-01-01", periods=36, freq="MS")
    frame = long_frame(dates, ["A", "B", "C", "LATE"])
    frame = frame[~((frame["sku"] == "LATE") & (frame["date"] < dates[6]))]
    panel = prepare_units(frame, Frequency.MONTHLY)
    assert panel.ts_ids == ["sku=A", "sku=B", "sku=C", "sku=LATE"]

    model = StandInForecaster()
    result = backtest(panel, model, first_cutoff="2025-01-01", step=5, horizon=6)
    # By January 2025 LATE has 19 months, under the minimum of 24. By June it has 24.
    assert model.calls == [(3, 25, 6), (4, 30, 6)]
    assert [len(issued.values) for issued in result.forecasts] == [3 * 6, 4 * 6]
    assert set(result.forecasts[0].values["ts_id"]) == {"sku=A", "sku=B", "sku=C"}
    by_series = result.by_series.set_index("ts_id")
    assert by_series["n"].to_dict() == {"sku=A": 12, "sku=B": 12, "sku=C": 12, "sku=LATE": 6}
    # Each scored row carries the actual of its own series.
    late = result.scored[result.scored["ts_id"] == "sku=LATE"]
    assert late["actual"].tolist() == [400.0 + i for i in range(30, 36)]


def test_a_date_with_no_row_is_scored_as_zero_when_the_dataset_says_so():
    dates = pd.date_range("2023-01-01", periods=36, freq="MS")
    frame = long_frame(dates, ["A", "B"])
    frame = frame[~((frame["sku"] == "A") & (frame["date"] == dates[27]))]
    result = backtest(prepare_units(frame, Frequency.MONTHLY, fill=Fill.ZERO))
    by_series = result.by_series.set_index("ts_id")
    assert by_series.loc["sku=A", "n"] == 12
    march = result.scored[
        (result.scored["ts_id"] == "sku=A") & (result.scored["date"] == dates[27])
    ]
    assert march["actual"].tolist() == [0.0]
