"""Ingest the demo data into Iceberg tables: clean cuts of Iowa liquor sales.

Run inside Cloudera AI, in a session or as an AMP step:

    python3 ingest/ingest_iowa.py

It downloads the years 2022 through 2026 (about 2.8 GB of order lines), sums
them with Spark, and writes two tables in which every series is complete and
all series have the same length:

- `iowa_liquor_monthly`: bottles, dollars and liters per month, county and
  item, for the county and item pairs that sold in every month.
- `iowa_liquor_weekly`: the same per week and item, statewide, for the items
  that sold in every week.

It then saves both as datasets, so the application lists them. Running it
again replaces the tables with fresh copies.

Settings, all optional, from environment variables:

- `U4CAST_DATABASE`: the database to write to. Default `u4cast`.
- `U4CAST_IOWA_YEARS`: the years to ingest, for example `2024,2025,2026`.
- `U4CAST_SPARK_CONNECTION`: the Spark data connection to use.
- `U4CAST_SPARK_MODE`: `local` or `cluster`; see `u4cast.storage.spark_settings`.

Source: Iowa Department of Revenue, Alcoholic Beverages Division, published
on the Iowa Data Hub under CC BY 4.0.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

sys.path.insert(0, str(Path(__file__).resolve().parent))

from iowa_download import DATASET_IDS, download
from u4cast.frequency import Frequency
from u4cast.layout import DatasetRecord
from u4cast.prepare import Fill
from u4cast.storage import Store, spark_session

if TYPE_CHECKING:
    from pyspark.sql import Column, DataFrame, SparkSession

logger = logging.getLogger("u4cast.ingest_iowa")

FOLDER = Path(__file__).resolve().parent.parent / "data" / "iowa"

# The columns of the published files, in order. Read as text unless named here.
COLUMNS = [
    "invoice_id",
    "ordered_on",
    "store_no",
    "store_name",
    "store_address",
    "store_city",
    "store_zip_code",
    "county_fips_code",
    "county_name",
    "category_code",
    "category_name",
    "vendor_number",
    "vendor_name",
    "item_no",
    "im_desc",
    "pack",
    "bottle_volume_ml",
    "state_bottle_cost",
    "state_bottle_retail",
    "sales_bottles",
    "sales_dollars",
    "sales_liters",
    "sales_gallons",
]
TYPES = {
    "ordered_on": "date",
    "sales_bottles": "double",
    "sales_dollars": "double",
    "sales_liters": "double",
}
MEASURES = ("bottles", "dollars", "liters")
# A month counts as whole when the data starts or ends within this many days
# of its first or last day. New Year's Day has no orders, for example.
MONTH_EDGE_DAYS = 3


@dataclass(frozen=True)
class Cut:
    """One clean table: its period, what a series is, and how it is listed."""

    table: str
    dataset: str
    frequency: Frequency
    date_column: str
    series_keys: tuple[str, ...]
    """The columns of the order lines whose combinations are the series."""
    dimensions: tuple[str, ...]
    """The dimension columns of the written table."""


MONTHLY = Cut(
    table="iowa_liquor_monthly",
    dataset="Iowa liquor sales, monthly",
    frequency=Frequency.MONTHLY,
    date_column="month",
    series_keys=("county", "item_no"),
    dimensions=("county", "category", "vendor", "item"),
)
WEEKLY = Cut(
    table="iowa_liquor_weekly",
    dataset="Iowa liquor sales, weekly",
    frequency=Frequency.WEEKLY,
    date_column="week",
    series_keys=("item_no",),
    dimensions=("category", "vendor", "item"),
)
CUTS = (MONTHLY, WEEKLY)


def read_orders(spark: SparkSession, folder: Path) -> DataFrame:
    """Read the downloaded CSV parts: one row per order line that names its date, store and item."""
    from pyspark.sql import functions as fn

    schema = ", ".join(f"{name} {TYPES.get(name, 'string')}" for name in COLUMNS)
    orders = spark.read.csv(
        f"file://{folder}/*.csv", header=True, schema=schema, quote='"', escape='"', multiLine=True
    )
    return orders.where(
        fn.col("ordered_on").isNotNull()
        & fn.col("store_no").isNotNull()
        & fn.col("item_no").isNotNull()
    )


def whole_periods(
    orders: DataFrame, frequency: Frequency, first_day: dt.date, last_day: dt.date
) -> DataFrame:
    """Add the column `period`, and drop the first or last period if the data covers it in part.

    A period is named by its first day: the Monday of a week, the first of a
    month. A partial period would look like a drop in sales.
    """
    from pyspark.sql import functions as fn

    if frequency is Frequency.WEEKLY:
        orders = orders.withColumn("period", fn.date_trunc("week", "ordered_on").cast("date"))
        first_is_whole = first_day.weekday() == 0  # a Monday
        last_is_whole = last_day.weekday() >= 4  # Friday or later
        period_of = _monday
    elif frequency is Frequency.MONTHLY:
        orders = orders.withColumn("period", fn.trunc("ordered_on", "month"))
        month_end = (last_day.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
        first_is_whole = first_day.day <= 1 + MONTH_EDGE_DAYS
        last_is_whole = (month_end - last_day).days <= 1 + MONTH_EDGE_DAYS
        period_of = _first_of_month
    else:
        raise ValueError(
            f"The Iowa ingest writes weekly and monthly tables, not {frequency.value}."
        )
    if not first_is_whole:
        orders = orders.where(fn.col("period") > fn.lit(period_of(first_day)))
    if not last_is_whole:
        orders = orders.where(fn.col("period") < fn.lit(period_of(last_day)))
    return orders


def _monday(day: dt.date) -> dt.date:
    return day - dt.timedelta(days=day.weekday())


def _first_of_month(day: dt.date) -> dt.date:
    return day.replace(day=1)


def clean_cut(orders: DataFrame, cut: Cut, first_day: dt.date, last_day: dt.date) -> DataFrame:
    """Sum order lines to the cut's periods and series, and keep the complete series.

    A series is complete when it has a row in every period. Only those are
    written, so every series in the table has the same length and no gaps. A
    store or an item carries the place, name, category and vendor of its most
    recent order, so a renamed item stays one series.
    """
    from pyspark.sql import functions as fn

    def latest(column: str, name: str) -> Column:
        return fn.max_by(fn.upper(fn.trim(column)), "ordered_on").alias(name)

    stores = orders.groupBy("store_no").agg(latest("county_name", "county"))
    items = orders.groupBy("item_no").agg(
        latest("im_desc", "item_name"),
        latest("category_name", "category"),
        latest("vendor_name", "vendor"),
    )
    keys = list(cut.series_keys)
    totals = (
        whole_periods(orders, cut.frequency, first_day, last_day)
        .join(fn.broadcast(stores), "store_no")
        .where(fn.col("county").isNotNull())
        .groupBy("period", *keys)
        .agg(*(fn.sum(f"sales_{measure}").alias(measure) for measure in MEASURES))
    )
    periods = totals.select("period").distinct().count()
    complete = (
        totals.groupBy(*keys)
        .agg(fn.count("*").alias("periods"))
        .where(fn.col("periods") == periods)
        .select(*keys)
    )
    # The number makes the label unique: two items can share a name.
    item = fn.concat(fn.col("item_name"), fn.lit(" ("), fn.col("item_no"), fn.lit(")"))
    return (
        totals.join(complete, keys, "left_semi")
        .join(fn.broadcast(items), "item_no")
        .withColumn("item", item)
        .select(fn.col("period").alias(cut.date_column), *cut.dimensions, *MEASURES)
    )


def dataset_record(cut: Cut, database: str) -> DatasetRecord:
    """Return the dataset the application lists for a cut."""
    return DatasetRecord(
        dataset=cut.dataset,
        source_table=f"{database}.{cut.table}",
        date_column=cut.date_column,
        target_column="bottles",
        dimension_columns=cut.dimensions,
        frequency=cut.frequency,
        # In order data, a period with no row is a period in which nothing was ordered.
        fill=Fill.ZERO,
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", stream=sys.stdout)
    years = sorted(
        int(year) for year in os.environ.get("U4CAST_IOWA_YEARS", "").split(",") if year.strip()
    ) or sorted(DATASET_IDS)
    parts = download(years, FOLDER)
    size = sum(part.stat().st_size for part in parts)
    logger.info("Downloaded %d files, %.2f GB, for %s", len(parts), size / 1e9, years)

    from pyspark.sql import functions as fn

    spark = spark_session()
    store = Store(spark)
    store.create_tables()
    orders = read_orders(spark, FOLDER)
    first_day, last_day = orders.agg(fn.min("ordered_on"), fn.max("ordered_on")).first()
    logger.info("Orders run from %s to %s", first_day, last_day)

    for cut in CUTS:
        table = f"{store.database}.{cut.table}"
        clean_cut(orders, cut, first_day, last_day).sortWithinPartitions(cut.date_column).writeTo(
            table
        ).using("iceberg").createOrReplace()
        written = spark.table(table)
        rows, periods, first, last, series = written.agg(
            fn.count("*"),
            fn.countDistinct(cut.date_column),
            fn.min(cut.date_column),
            fn.max(cut.date_column),
            fn.countDistinct(*cut.dimensions),
        ).first()
        logger.info(
            "Wrote %s: %d rows, %d series of %d %s periods, from %s to %s",
            table,
            rows,
            series,
            periods,
            cut.frequency.value,
            first,
            last,
        )
        if rows != series * periods:
            raise RuntimeError(f"{table} is not a clean cut: {rows} rows for {series} series.")
        store.save_dataset(dataset_record(cut, store.database), replace=True)
    spark.stop()


if __name__ == "__main__":
    main()
