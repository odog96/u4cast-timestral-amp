"""A history table that grows, to show a forecast in production on a compressed clock.

In real use a month or a week passes between two runs of a forecast in
production. To watch it work in an hour, this makes a copy of the weekly demo
table that stops some weeks early, and then adds one more week of the real
data each time it is run. A job runs it every ten minutes, so ten minutes
stand for a week.

    .venv/bin/python production/proof_data.py start    # make the table and the dataset
    .venv/bin/python production/proof_data.py          # add the next week

The table is `proof_weekly_sales` in the application's database and the
dataset is called "Weekly sales, arriving". Both are for demonstration.
"""

from __future__ import annotations

import logging
import sys

from u4cast.frequency import Frequency
from u4cast.layout import DatasetRecord
from u4cast.prepare import Fill
from u4cast.storage import Store, spark_session

logger = logging.getLogger("u4cast.proof")

SOURCE = "iowa_liquor_weekly"
TABLE = "proof_weekly_sales"
DATASET = "Weekly sales, arriving"
WEEKS_HELD_BACK = 10


def start(store: Store) -> None:
    """Make the table with all but the last weeks of the source, and save the dataset."""
    spark, database = store.spark, store.database
    weeks = [r[0] for r in spark.sql(f"select distinct week from {database}.{SOURCE}").collect()]
    cut = sorted(weeks)[-WEEKS_HELD_BACK - 1]
    spark.sql(f"drop table if exists {database}.{TABLE}")
    spark.sql(
        f"create table {database}.{TABLE} using iceberg as "
        f"select * from {database}.{SOURCE} where week <= date'{cut}'"
    )
    store.save_dataset(
        DatasetRecord(
            dataset=DATASET,
            source_table=f"{database}.{TABLE}",
            date_column="week",
            target_column="bottles",
            dimension_columns=("category", "vendor", "item"),
            frequency=Frequency.WEEKLY,
            fill=Fill.ZERO,
        ),
        replace=True,
    )
    logger.info(
        "Made %s.%s with history to %s; %d weeks are held back",
        database,
        TABLE,
        cut,
        WEEKS_HELD_BACK,
    )


def add_a_week(store: Store) -> None:
    """Append the next week of the source that the table does not have yet."""
    spark, database = store.spark, store.database
    last = spark.sql(f"select max(week) from {database}.{TABLE}").collect()[0][0]
    following = spark.sql(
        f"select min(week) from {database}.{SOURCE} where week > date'{last}'"
    ).collect()[0][0]
    if following is None:
        logger.info("The table has every week of the source. Nothing to add.")
        return
    spark.sql(
        f"insert into {database}.{TABLE} "
        f"select * from {database}.{SOURCE} where week = date'{following}'"
    )
    logger.info("Added the week of %s", following)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
    proof_store = Store(spark_session())
    proof_store.create_tables()
    (start if sys.argv[1:] == ["start"] else add_a_week)(proof_store)
