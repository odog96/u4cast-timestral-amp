"""The layout of the stored tables."""

import datetime as dt
import json

import pytest

from u4cast.frequency import Frequency
from u4cast.layout import (
    DATASETS_TABLE,
    FORECASTS_TABLE,
    DatasetRecord,
    ForecastRecord,
    Kind,
    forecast_values_columns,
    forecast_values_table,
    values_table_schema,
)


def test_forecast_values_table_has_the_dimension_columns_of_its_dataset():
    assert forecast_values_columns(["store", "category"]) == (
        "forecast_id",
        "ts_id",
        "store",
        "category",
        "date",
        "forecast",
        "low",
        "high",
        "step",
    )
    assert forecast_values_columns([]) == (
        "forecast_id",
        "ts_id",
        "date",
        "forecast",
        "low",
        "high",
        "step",
    )


@pytest.mark.parametrize(
    "name", ["forecast_id", "ts_id", "date", "forecast", "low", "high", "step"]
)
def test_a_dimension_column_cannot_reuse_a_stored_column_name(name):
    with pytest.raises(ValueError, match=f"cannot be named {name}"):
        forecast_values_columns(["store", name])


def test_forecasts_table_columns():
    assert [name for name, _ in FORECASTS_TABLE] == [
        "forecast_id",
        "forecast_name",
        "dataset",
        "target_column",
        "dimension_columns",
        "slice_filter",
        "frequency",
        "history_start_date",
        "history_end_date",
        "horizon",
        "model_name",
        "kind",
        "created_at",
    ]


def test_datasets_table_row():
    record = DatasetRecord(
        dataset="liquor",
        source_table="demo.iowa_liquor_sales",
        date_column="month",
        target_column="bottles_sold",
        dimension_columns=("store", "category"),
        frequency=Frequency.MONTHLY,
    )
    row = record.to_row()
    assert list(row) == [name for name, _ in DATASETS_TABLE]
    assert row["dataset"] == "liquor"
    assert row["source_table"] == "demo.iowa_liquor_sales"
    assert json.loads(row["dimension_columns"]) == ["store", "category"]
    assert row["frequency"] == "monthly"


def test_each_dataset_has_its_own_forecast_values_table():
    assert forecast_values_table("Iowa liquor sales") == "forecast_values_iowa_liquor_sales"
    assert forecast_values_table("  Sales 2026 (v2)! ") == "forecast_values_sales_2026_v2"
    with pytest.raises(ValueError, match="no letter or digit"):
        forecast_values_table("???")


def test_dimension_columns_are_stored_as_text():
    assert values_table_schema(["store", "category"]) == [
        ("forecast_id", "string"),
        ("ts_id", "string"),
        ("store", "string"),
        ("category", "string"),
        ("date", "date"),
        ("forecast", "double"),
        ("low", "double"),
        ("high", "double"),
        ("step", "int"),
    ]


def test_records_survive_being_stored_as_rows():
    dataset = DatasetRecord(
        dataset="liquor",
        source_table="demo.iowa_liquor_weekly",
        date_column="week",
        target_column="bottles",
        dimension_columns=("store", "item"),
        frequency=Frequency.WEEKLY,
    )
    assert DatasetRecord.from_row(dataset.to_row()) == dataset
    forecast = ForecastRecord(
        forecast_id="abc",
        forecast_name="Weekly demand",
        dataset="liquor",
        target_column="bottles",
        dimension_columns=("store", "item"),
        slice_filter={"county": ["POLK", "LINN"], "store": [2633]},
        frequency=Frequency.WEEKLY,
        history_start_date=dt.date(2022, 1, 3),
        history_end_date=dt.date(2026, 10, 26),
        horizon=13,
        model_name="mistralai/Timestral-209M",
        kind=Kind.BACKTEST,
        created_at=dt.datetime(2026, 11, 17, 22, 0, 0),
    )
    assert ForecastRecord.from_row(forecast.to_row()) == forecast
