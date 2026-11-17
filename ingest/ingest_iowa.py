"""Ingest the demo data into an Iceberg table: Iowa liquor sales, weekly.

Run inside Cloudera AI, in a session or as an AMP step:

    python3 ingest/ingest_iowa.py

It downloads the years 2022 through 2026 (about 2.8 GB), sums the order lines
to weekly totals per store and item with Spark, writes them to the table
`iowa_liquor_weekly`, and saves the dataset so the application lists it.
Running it again replaces the table with a fresh copy.

Settings, all optional, from environment variables:

- `U4CAST_DATABASE`: the database to write to. Default `u4cast`.
- `U4CAST_IOWA_YEARS`: the years to ingest, for example `2024,2025,2026`.
- `U4CAST_SPARK_CONNECTION`: the Spark data connection to use.

Source: Iowa Department of Revenue, Alcoholic Beverages Division, published
on the Iowa Data Hub under CC BY 4.0.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

sys.path.insert(0, str(Path(__file__).resolve().parent))

from iowa_download import DATASET_IDS, download

from u4cast.frequency import Frequency
from u4cast.layout import DatasetRecord
from u4cast.prepare import Fill
from u4cast.storage import Store, spark_session

if TYPE_CHECKING:
    from pyspark.sql import DataFrame, SparkSession

logger = logging.getLogger("u4cast.ingest_iowa")

TABLE = "iowa_liquor_weekly"
DATASET = "Iowa liquor sales"
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
DIMENSIONS = ("county", "city", "store", "category", "vendor", "item")


def read_orders(spark: SparkSession, folder: Path) -> DataFrame:
    """Read the downloaded CSV parts: one row per order line."""
    schema = ", ".join(f"{name} {TYPES.get(name, 'string')}" for name in COLUMNS)
    return spark.read.csv(
        f"file://{folder}/*.csv", header=True, schema=schema, quote='"', escape='"', multiLine=True
    )


def weekly_totals(orders: DataFrame) -> DataFrame:
    """Sum order lines to one row per week, store and item.

    A week starts on Monday and is named by that date. The first and the last
    week are dropped when the data does not cover them whole, because a
    partial week would look like a drop in sales. A store or an item keeps the
    name, place, category and vendor of its most recent order, so a renamed
    store stays one series.
    """
    from pyspark.sql import functions as fn

    orders = orders.where(
        fn.col("ordered_on").isNotNull()
        & fn.col("store_no").isNotNull()
        & fn.col("item_no").isNotNull()
    ).withColumn("week", fn.date_trunc("week", "ordered_on").cast("date"))

    first_day, last_day = orders.agg(fn.min("ordered_on"), fn.max("ordered_on")).first()
    if first_day.weekday() != 0:  # not a Monday: the first week is partial
        orders = orders.where(fn.col("week") > fn.lit(first_day))
    if last_day.weekday() < 4:  # ends before Friday: the last week is partial
        orders = orders.where(fn.date_add("week", 6) <= fn.lit(last_day))
    logger.info("Orders run from %s to %s", first_day, last_day)

    def latest(column: str, name: str) -> object:
        return fn.max_by(fn.upper(fn.trim(column)), "ordered_on").alias(name)

    def label(name: str, number: str, alias: str) -> object:
        # The number makes the label unique: two stores can share a name.
        return fn.concat(fn.col(name), fn.lit(" ("), fn.col(number), fn.lit(")")).alias(alias)

    stores = orders.groupBy("store_no").agg(
        latest("store_name", "store_name"),
        latest("store_city", "city"),
        latest("county_name", "county"),
    )
    items = orders.groupBy("item_no").agg(
        latest("im_desc", "item_name"),
        latest("category_name", "category"),
        latest("vendor_name", "vendor"),
    )
    totals = orders.groupBy("week", "store_no", "item_no").agg(
        fn.sum("sales_bottles").alias("bottles"),
        fn.sum("sales_dollars").alias("dollars"),
        fn.sum("sales_liters").alias("liters"),
    )
    return (
        totals.join(fn.broadcast(stores), "store_no")
        .join(fn.broadcast(items), "item_no")
        .select(
            "week",
            "county",
            "city",
            label("store_name", "store_no", "store"),
            "category",
            "vendor",
            label("item_name", "item_no", "item"),
            "bottles",
            "dollars",
            "liters",
        )
    )


def dataset_record(table: str) -> DatasetRecord:
    """Return the dataset the application lists for the ingested table."""
    return DatasetRecord(
        dataset=DATASET,
        source_table=table,
        date_column="week",
        target_column="bottles",
        dimension_columns=DIMENSIONS,
        frequency=Frequency.WEEKLY,
        # A week with no row is a week in which nothing was ordered.
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

    spark = spark_session()
    store = Store(spark)
    store.create_tables()
    table = f"{store.database}.{TABLE}"
    weekly = weekly_totals(read_orders(spark, FOLDER))
    weekly.sortWithinPartitions("week").writeTo(table).using("iceberg").createOrReplace()

    from pyspark.sql import functions as fn

    written = spark.table(table)
    counts = written.agg(
        fn.count("*"),
        fn.countDistinct("week"),
        fn.min("week"),
        fn.max("week"),
        fn.countDistinct("store"),
        fn.countDistinct("item"),
        fn.countDistinct("store", "item"),
        fn.countDistinct("category"),
        fn.countDistinct("county"),
    ).first()
    logger.info(
        "Wrote %s: %d rows, %d weeks from %s to %s, %d stores, %d items, "
        "%d store and item pairs, %d categories, %d counties",
        table,
        *counts,
    )

    store.save_dataset(dataset_record(table), replace=True)
    spark.stop()


if __name__ == "__main__":
    main()
