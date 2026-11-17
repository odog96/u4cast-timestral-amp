"""Issuing forecasts: the rows that are stored, and tracing reissued forecasts."""

import datetime as dt
import json

import numpy as np
import pandas as pd
import pytest

from conftest import long_frame, prepare_units
from u4cast.forecast import issue_forecast, reissue_forecast
from u4cast.frequency import Frequency
from u4cast.layout import FORECASTS_TABLE, Kind
from u4cast.model import StandInForecaster
from u4cast.prepare import prepare


def issue(panel, **options):
    options = {
        "horizon": 12,
        "forecast_name": "Monthly demand",
        "dataset": "liquor",
        "target_column": "units",
    } | options
    return issue_forecast(panel, StandInForecaster(), **options)


def test_values_have_one_row_per_series_and_future_date(monthly_panel):
    issued = issue(monthly_panel, horizon=12)
    values = issued.values
    assert values.columns.tolist() == [
        "forecast_id",
        "ts_id",
        "sku",
        "date",
        "forecast",
        "low",
        "high",
        "step",
    ]
    assert len(values) == 3 * 12
    assert not values.duplicated(["forecast_id", "ts_id", "date"]).any()
    assert values["forecast_id"].unique().tolist() == [issued.record.forecast_id]
    assert values.groupby("ts_id")["step"].apply(list).tolist() == [list(range(1, 13))] * 3


def test_future_dates_continue_the_grid_after_the_end_of_history(monthly_panel):
    values = issue(monthly_panel, horizon=3).values
    first_series = values[values["ts_id"] == "sku=A"]
    assert first_series["date"].tolist() == list(
        pd.to_datetime(["2026-01-01", "2026-02-01", "2026-03-01"])
    )
    assert first_series["step"].tolist() == [1, 2, 3]


def test_each_row_carries_the_values_and_dimensions_of_its_own_series(monthly_panel):
    values = issue(monthly_panel, horizon=2).values
    # The stand-in repeats a series' last value: 100 * (s + 1) + 35.
    by_series = values.groupby("ts_id").first()
    assert by_series["forecast"].to_dict() == {"sku=A": 135.0, "sku=B": 235.0, "sku=C": 335.0}
    assert by_series["sku"].to_dict() == {"sku=A": "A", "sku=B": "B", "sku=C": "C"}
    assert (values["low"] <= values["forecast"]).all()
    assert (values["forecast"] <= values["high"]).all()


def test_record_says_what_the_forecast_is(monthly_panel):
    created = dt.datetime(2026, 1, 5, 9, 30, tzinfo=dt.UTC)
    issued = issue(monthly_panel, slice_filter={"sku": ["A", "B", "C"]}, created_at=created)
    record = issued.record
    assert record.forecast_name == "Monthly demand"
    assert record.dataset == "liquor"
    assert record.target_column == "units"
    assert record.dimension_columns == ("sku",)
    assert record.frequency is Frequency.MONTHLY
    assert record.history_start_date == dt.date(2023, 1, 1)
    assert record.history_end_date == dt.date(2025, 12, 1)
    assert record.horizon == 12
    assert record.model_name == "stand-in"
    assert record.kind is Kind.FORECAST
    assert record.created_at == created

    row = record.to_row()
    assert list(row) == [name for name, _ in FORECASTS_TABLE]
    assert json.loads(row["dimension_columns"]) == ["sku"]
    assert json.loads(row["slice_filter"]) == {"sku": ["A", "B", "C"]}
    assert row["frequency"] == "monthly"
    assert row["kind"] == "forecast"


def test_every_forecast_gets_its_own_id(monthly_panel):
    ids = {issue(monthly_panel).record.forecast_id for _ in range(5)}
    assert len(ids) == 5


def test_history_steps_forecasts_from_an_earlier_date(monthly_panel):
    issued = issue(monthly_panel, horizon=2, history_steps=30, kind=Kind.BACKTEST)
    assert issued.record.history_end_date == dt.date(2025, 6, 1)
    assert issued.record.kind is Kind.BACKTEST
    assert issued.values["date"].min() == pd.Timestamp("2025-07-01")
    # Only the first 30 steps were given to the model: value 100 + 29, not 100 + 35.
    assert issued.values.loc[issued.values["ts_id"] == "sku=A", "forecast"].iloc[0] == 129.0


def test_history_below_the_minimum_is_refused(monthly_panel):
    with pytest.raises(ValueError, match="between 24 and 36"):
        issue(monthly_panel, history_steps=23)
    with pytest.raises(ValueError, match="between 24 and 36"):
        issue(monthly_panel, history_steps=37)


def test_a_panel_with_every_series_left_out_is_refused():
    dates = pd.date_range("2023-01-01", periods=12, freq="MS")
    panel = prepare_units(long_frame(dates, ["A"]), Frequency.MONTHLY)
    with pytest.raises(ValueError, match="no series to forecast"):
        issue(panel)


def test_a_dimension_column_cannot_take_the_name_of_a_stored_column():
    dates = pd.date_range("2023-01-01", periods=30, freq="MS")
    frame = long_frame(dates, ["A"]).rename(columns={"sku": "step", "date": "month"})
    panel = prepare(
        frame,
        date_column="month",
        target_column="units",
        dimension_columns=["step"],
        frequency=Frequency.MONTHLY,
    )
    with pytest.raises(ValueError, match="cannot be named step"):
        issue(panel)


def test_reissued_forecasts_overlap_and_stay_traceable():
    """Oliver's example: 100 SKUs, monthly, 12 months ahead, reissued every month."""
    skus = [f"SKU{n:03d}" for n in range(100)]
    all_months = pd.date_range("2023-01-01", "2026-03-01", freq="MS")
    data = long_frame(all_months, skus)
    model = StandInForecaster()

    def panel_as_of(last_month: str):
        return prepare_units(data, Frequency.MONTHLY, history_end=last_month)

    january = issue_forecast(
        panel_as_of("2025-12-01"),
        model,
        horizon=12,
        forecast_name="Monthly demand",
        dataset="liquor",
        target_column="units",
        slice_filter={"sku": skus},
    )
    february = reissue_forecast(january.record, panel_as_of("2026-01-01"), model)
    march = reissue_forecast(february.record, panel_as_of("2026-02-01"), model)
    issues = [january, february, march]

    assert [len(issue.values) for issue in issues] == [1200, 1200, 1200]
    assert len({issue.record.forecast_id for issue in issues}) == 3
    assert {issue.record.forecast_name for issue in issues} == {"Monthly demand"}
    assert all(issue.record.slice_filter == {"sku": skus} for issue in issues)
    assert [issue.record.history_end_date for issue in issues] == [
        dt.date(2025, 12, 1),
        dt.date(2026, 1, 1),
        dt.date(2026, 2, 1),
    ]

    stored = pd.concat([issue.values for issue in issues], ignore_index=True)
    june = stored[
        (stored["date"] == pd.Timestamp("2026-06-01")) & (stored["ts_id"] == "sku=SKU042")
    ]
    assert len(june) == 3
    assert set(june["forecast_id"]) == {issue.record.forecast_id for issue in issues}
    assert sorted(june["step"]) == [4, 5, 6]
    # A series has the same ts_id in every issue.
    assert all(set(issue.values["ts_id"]) == {f"sku={sku}" for sku in skus} for issue in issues)
    assert np.isfinite(stored[["forecast", "low", "high"]].to_numpy()).all()


def test_a_reissue_must_be_of_the_same_slice_layout(monthly_panel):
    first = issue(monthly_panel)
    dates = pd.date_range("2023-01-01", periods=36, freq="MS")
    frame = long_frame(dates, ["A"]).assign(store=1)
    other_dimensions = prepare(
        frame,
        date_column="date",
        target_column="units",
        dimension_columns=["store", "sku"],
        frequency=Frequency.MONTHLY,
    )
    with pytest.raises(ValueError, match="same dimension columns"):
        reissue_forecast(first.record, other_dimensions, StandInForecaster())
    quarterly = prepare_units(
        long_frame(pd.date_range("2020-01-01", periods=12, freq="QS"), ["A"]), Frequency.QUARTERLY
    )
    with pytest.raises(ValueError, match="same frequency"):
        reissue_forecast(first.record, quarterly, StandInForecaster())
