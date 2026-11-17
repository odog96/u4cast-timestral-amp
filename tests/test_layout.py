"""The layout of the stored tables."""

import json

import pytest

from u4cast.frequency import Frequency
from u4cast.layout import (
    DATASETS_TABLE,
    FORECASTS_TABLE,
    DatasetRecord,
    forecast_values_columns,
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
