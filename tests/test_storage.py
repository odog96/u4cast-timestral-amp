"""The storage layer against real Spark and Iceberg. Skipped unless asked for.

Run inside Cloudera AI with `U4CAST_TEST_SPARK=1 pytest -m spark`. It works in
a scratch database that it creates and drops.
"""

import datetime as dt
import os
import uuid

import pandas as pd
import pytest

from conftest import long_frame
from u4cast.backtest import run_backtest
from u4cast.forecast import issue_forecast, reissue_forecast
from u4cast.frequency import Frequency
from u4cast.layout import DatasetRecord, Kind
from u4cast.model import StandInForecaster
from u4cast.prepare import prepare
from u4cast.storage import Store, spark_session

pytestmark = [
    pytest.mark.spark,
    pytest.mark.skipif(
        os.environ.get("U4CAST_TEST_SPARK") != "1", reason="set U4CAST_TEST_SPARK=1 to run"
    ),
]

MONTHS = pd.date_range("2023-01-01", periods=36, freq="MS")


@pytest.fixture(scope="module")
def store():
    spark = spark_session()
    scratch = Store(spark, f"u4cast_test_{uuid.uuid4().hex[:8]}")
    scratch.create_tables()
    yield scratch
    for row in spark.sql(f"show tables in {scratch.database}").collect():
        spark.sql(f"drop table if exists {scratch.database}.{row.tableName} purge")
    spark.sql(f"drop database if exists {scratch.database}")


@pytest.fixture(scope="module")
def dataset(store):
    """Two stores by three SKUs, monthly, as an uploaded table and a saved dataset."""
    history = pd.concat(
        [long_frame(MONTHS, ["A", "B", "C"]).assign(store=number) for number in (1, 2)]
    ).rename(columns={"date": "month"})
    history["month"] = history["month"].dt.date
    table = store.create_table_from_frame("sales", history)
    record = DatasetRecord(
        dataset="Monthly sales",
        source_table=table,
        date_column="month",
        target_column="units",
        dimension_columns=("store", "sku"),
        frequency=Frequency.MONTHLY,
    )
    store.save_dataset(record)
    return record


def panel_of(store, dataset, **options):
    dimension_columns = options.pop("dimension_columns", dataset.dimension_columns)
    rows = store.load_slice(
        dataset.source_table,
        date_column=dataset.date_column,
        target_column=dataset.target_column,
        dimension_columns=dimension_columns,
        **options,
    )
    return prepare(
        rows,
        date_column=dataset.date_column,
        target_column=dataset.target_column,
        dimension_columns=dimension_columns,
        frequency=dataset.frequency,
    )


def test_tables_can_be_created_twice(store):
    store.create_tables()


def test_a_saved_dataset_is_listed_and_its_name_cannot_be_reused(store, dataset):
    assert store.datasets() == [dataset]
    assert store.dataset("Monthly sales") == dataset
    with pytest.raises(ValueError, match="already saved"):
        store.save_dataset(dataset)
    with pytest.raises(KeyError, match="No dataset named"):
        store.dataset("Other")


def test_an_upload_never_replaces_a_table_that_exists(store, dataset):
    with pytest.raises(ValueError, match="already exists"):
        store.create_table_from_frame("sales", pd.DataFrame({"a": [1]}))


def test_columns_values_and_dates_of_a_history_table(store, dataset):
    assert dict(store.columns(dataset.source_table)) == {
        "month": "date",
        "sku": "string",
        "units": "double",
        "store": "bigint",
    }
    assert store.dimension_values(dataset.source_table, "sku") == ["A", "B", "C"]
    assert store.dimension_values(dataset.source_table, "store") == [1, 2]
    assert store.date_range(dataset.source_table, "month") == (
        dt.date(2023, 1, 1),
        dt.date(2025, 12, 1),
    )


def test_a_slice_is_filtered_summed_and_limited_in_the_query(store, dataset):
    rows = store.load_slice(
        dataset.source_table,
        date_column="month",
        target_column="units",
        dimension_columns=["sku"],
        slice_filter={"sku": ["A", "C"]},
        history_start="2024-01-01",
        history_end="2025-06-01",
    )
    assert sorted(rows.columns) == ["month", "sku", "units"]
    assert len(rows) == 2 * 18
    assert set(rows["sku"]) == {"A", "C"}
    assert rows["month"].min() == pd.Timestamp("2024-01-01")
    assert rows["month"].max() == pd.Timestamp("2025-06-01")
    # SKU A is 100 + i in each of the two stores; January 2024 is i = 12.
    first = rows[(rows["sku"] == "A") & (rows["month"] == pd.Timestamp("2024-01-01"))]
    assert first["units"].tolist() == [2 * 112.0]


def test_a_forecast_is_stored_and_read_back_unchanged(store, dataset):
    panel = panel_of(store, dataset)
    issued = issue_forecast(
        panel,
        StandInForecaster(),
        horizon=12,
        forecast_name="Stored",
        dataset=dataset.dataset,
        target_column="units",
        slice_filter={"store": [1, 2]},
    )
    store.save_forecast(issued, dataset)

    (record,) = store.forecasts(forecast_name="Stored")
    assert record.forecast_id == issued.record.forecast_id
    assert record.slice_filter == {"store": [1, 2]}
    assert record.dimension_columns == ("store", "sku")
    assert record.frequency is Frequency.MONTHLY
    assert record.history_end_date == dt.date(2025, 12, 1)
    assert (record.horizon, record.kind, record.model_name) == (12, Kind.FORECAST, "stand-in")

    values = store.forecast_values(dataset.dataset, [record.forecast_id])
    expected = issued.values.sort_values(["ts_id", "step"], ignore_index=True)
    assert len(values) == 6 * 12
    assert values.columns.tolist() == expected.columns.tolist()
    assert values["ts_id"].tolist() == expected["ts_id"].tolist()
    assert values["date"].tolist() == expected["date"].tolist()
    assert values["forecast"].tolist() == expected["forecast"].tolist()
    assert values["step"].tolist() == expected["step"].tolist()
    assert values["store"].tolist() == expected["store"].astype(str).tolist()


def test_reissues_and_coarser_forecasts_are_appended_never_overwritten(store, dataset):
    by_sku = panel_of(store, dataset, dimension_columns=["sku"], history_end="2025-10-01")
    first = issue_forecast(
        by_sku,
        StandInForecaster(),
        horizon=6,
        forecast_name="By SKU",
        dataset=dataset.dataset,
        target_column="units",
    )
    store.save_forecast(first, dataset)
    before = store.forecast_values(dataset.dataset, [first.record.forecast_id])

    second = reissue_forecast(
        first.record,
        panel_of(store, dataset, dimension_columns=["sku"], history_end="2025-11-01"),
        StandInForecaster(),
    )
    store.save_forecast(second, dataset)

    issues = store.forecasts(forecast_name="By SKU")
    assert [issue.history_end_date for issue in issues] == [
        dt.date(2025, 11, 1),
        dt.date(2025, 10, 1),
    ]
    after = store.forecast_values(dataset.dataset, [first.record.forecast_id])
    pd.testing.assert_frame_equal(before, after)
    both = store.forecast_values(
        dataset.dataset, [first.record.forecast_id, second.record.forecast_id]
    )
    assert len(both) == 2 * 3 * 6
    # Summed up to SKU: the store column of the dataset is left empty.
    assert both["store"].isna().all()
    january = both[(both["date"] == pd.Timestamp("2026-01-01")) & (both["ts_id"] == "sku=B")]
    assert sorted(january["step"]) == [2, 3]


def test_backtests_are_stored_in_the_same_tables_marked_by_kind(store, dataset):
    result = run_backtest(
        panel_of(store, dataset),
        StandInForecaster(),
        first_cutoff="2025-01-01",
        step=3,
        horizon=6,
        forecast_name="Checked",
        dataset=dataset.dataset,
        target_column="units",
    )
    for issued in result.forecasts:
        store.save_forecast(issued, dataset)
    backtests = store.forecasts(dataset=dataset.dataset, kind=Kind.BACKTEST)
    assert [record.history_end_date for record in backtests] == [
        dt.date(2025, 4, 1),
        dt.date(2025, 1, 1),
    ]
    assert all(record.forecast_name == "Checked" for record in backtests)
    assert not store.forecasts(forecast_name="Checked", kind=Kind.FORECAST)
