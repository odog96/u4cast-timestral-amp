"""Move stored forecasts from one table per dataset to one table per forecast.

Until 19 November 2026 every forecast of a dataset was stored in one table,
`forecast_values_<dataset>`. Since then each forecast has its own, and its
test runs a second one. This copies the rows of every stored run to where
they now belong. Run it once, in a session:

    .venv/bin/python tools/move_to_table_per_forecast.py

It reads the old tables and never changes them. A run whose rows are already
in its new table is left alone, so running it twice is safe. A new install
does not need it.
"""

from __future__ import annotations

import logging

from pyspark.sql import functions as fn

from u4cast.layout import forecast_table, forecast_values_table, values_table_schema
from u4cast.storage import Store, _table, spark_session

logger = logging.getLogger("u4cast.move")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
    store = Store(spark_session())
    spark, database = store.spark, store.database
    moved = skipped = 0
    for record in sorted(store.forecasts(), key=lambda r: r.created_at):
        old = forecast_values_table(record.dataset)
        new = forecast_table(record.dataset, record.forecast_name, record.kind)
        if not spark.catalog.tableExists(f"{database}.{old}"):
            logger.info("%s has no table of the old kind", record.dataset)
            continue
        columns = values_table_schema(record.dimension_columns)
        store.ensure_table(new, columns)
        mine = fn.col("forecast_id") == record.forecast_id
        already = spark.table(_table(database, new)).where(mine).count()
        rows = spark.table(_table(database, old)).where(mine).select(*[c for c, _ in columns])
        count = rows.count()
        if already == count:
            skipped += 1
            continue
        if already:
            raise RuntimeError(f"{new} has {already} of the {count} rows of {record.forecast_id}.")
        rows.writeTo(_table(database, new)).append()
        moved += 1
        logger.info("%s %s: %d rows to %s", record.kind.value, record.history_end_date, count, new)
    logger.info("Moved %d runs. %d were already in place.", moved, skipped)


if __name__ == "__main__":
    main()
