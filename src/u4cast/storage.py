"""Read history and store forecasts in Iceberg tables, through Spark.

History is read and never written. A forecast is made for a slice: the filter
on dimension values, the summing up to the chosen dimension columns and the
history window all run in the query, so only the slice comes back.

Forecasts are stored in three kinds of table, all in one database:

- `forecasts`: one row per forecast.
- `datasets`: one row per saved dataset.
- `forecast_values_<dataset>`: one row per forecast, series and future date.
  New forecasts are appended. Nothing is overwritten.

The layout of the rows is in `u4cast.layout`.
"""

from __future__ import annotations

import datetime as dt
import logging
import math
import os
import re
from collections.abc import Mapping, MutableMapping, Sequence
from typing import TYPE_CHECKING, Any

import pandas as pd

from u4cast.forecast import IssuedForecast
from u4cast.layout import (
    DATASETS_TABLE,
    FORECASTS_TABLE,
    DatasetRecord,
    ForecastRecord,
    Kind,
    forecast_values_table,
    values_table_schema,
)
from u4cast.prepare import TS_ID
from u4cast.production import RUNS_TABLE, SCHEDULES_TABLE, Schedule

OWN_TABLES = ("forecasts", "datasets", "schedules", "production_runs")
"""The tables the application itself writes, next to one forecast values table per dataset."""

if TYPE_CHECKING:
    from pyspark.sql import DataFrame, SparkSession

logger = logging.getLogger(__name__)

DEFAULT_DATABASE = "u4cast"
SPARK_LOCAL = "local"
SPARK_CLUSTER = "cluster"


def spark_settings(environ: Mapping[str, str]) -> dict[str, str]:
    """Return the Spark settings this project needs, read from the environment.

    `U4CAST_SPARK_MODE` says where Spark runs:

    - `local`, the default: inside this container, with no separate workers.
      It starts in 7 seconds, and nothing can be lost on the way. It is the
      right size for this project: a slice is thousands of series, not
      billions of rows.
    - `cluster`: Cloudera AI starts worker containers. Capped at
      `U4CAST_SPARK_MAX_EXECUTORS` workers, 8 by default, because the
      platform's own default lets one job ask for 249.

    `U4CAST_SPARK_DRIVER_MEMORY`, 2g by default, caps Spark's own memory in
    this container. The platform's default hands Spark most of the container,
    which leaves too little for the web server and the model.
    """
    mode = environ.get("U4CAST_SPARK_MODE", SPARK_LOCAL)
    if mode not in (SPARK_LOCAL, SPARK_CLUSTER):
        raise ValueError(f"U4CAST_SPARK_MODE must be {SPARK_LOCAL} or {SPARK_CLUSTER}; got {mode}.")
    settings = {"spark.driver.memory": environ.get("U4CAST_SPARK_DRIVER_MEMORY", "2g")}
    if mode == SPARK_LOCAL:
        cores = max(1, math.ceil(int(environ.get("CDSW_CPU_MILLICORES", "2000")) / 1000))
        settings["spark.master"] = f"local[{cores}]"
        settings["spark.sql.shuffle.partitions"] = str(4 * cores)
    else:
        settings["spark.executor.memory"] = environ.get("U4CAST_SPARK_EXECUTOR_MEMORY", "2g")
        settings["spark.dynamicAllocation.maxExecutors"] = environ.get(
            "U4CAST_SPARK_MAX_EXECUTORS", "8"
        )
    return settings


def spark_environment(environ: MutableMapping[str, str]) -> None:
    """Point Spark at the data lake's catalog, where Cloudera AI has not.

    Spark reaches the catalog through the Hadoop settings and the Hive
    client libraries. Cloudera AI puts both on Spark's path in a session, but
    not in an application's container, where Spark then starts an empty
    catalog of its own. Setting them here makes both places work.
    """
    settings = environ.setdefault("HADOOP_CONF_DIR", "/etc/hadoop/conf")
    libraries = f"{environ.get('SPARK_HOME', '/opt/spark')}/optional-lib/*"
    path = [part for part in environ.get("SPARK_DIST_CLASSPATH", "").split(":") if part]
    path += [part for part in (libraries, settings) if part not in path]
    environ["SPARK_DIST_CLASSPATH"] = ":".join(path)


def spark_session(connection_name: str | None = None) -> SparkSession:
    """Return the Spark session of the project's Spark data connection.

    Runs in Cloudera AI only. The connection is `connection_name`, or the
    environment variable `U4CAST_SPARK_CONNECTION`, or the project's first
    Spark data connection. `spark_settings` says how Spark runs. The
    application starts one session and keeps it.
    """
    import cml.data_v1 as cmldata
    from pyspark.sql import SparkSession

    name = connection_name or os.environ.get("U4CAST_SPARK_CONNECTION")
    if not name:
        spark_connections = [
            connection["name"]
            for connection in cmldata.get_project_dataconnections()
            if connection.get("type") == "SPARK"
        ]
        if not spark_connections:
            raise RuntimeError("This project has no Spark data connection.")
        name = spark_connections[0]
    # These have to be in place before Spark starts, and Cloudera AI's library
    # builds the session itself, so they go in as command-line settings.
    spark_environment(os.environ)
    if "PYSPARK_SUBMIT_ARGS" not in os.environ and SparkSession.getActiveSession() is None:
        settings = [
            f"--conf {setting}={value}" for setting, value in spark_settings(os.environ).items()
        ]
        os.environ["PYSPARK_SUBMIT_ARGS"] = " ".join([*settings, "pyspark-shell"])
    logger.info("Starting Spark on the data connection %s", name)
    return cmldata.get_connection(name).get_spark_session()


def _quoted(name: str) -> str:
    return "`" + name.replace("`", "``") + "`"


def _table(database: str, table: str) -> str:
    return f"{_quoted(database)}.{_quoted(table)}"


def _source(table: str) -> str:
    """Quote a table name given as `table` or `database.table`."""
    return ".".join(_quoted(part) for part in table.split("."))


class Store:
    """The tables of one database, read and written through one Spark session."""

    def __init__(self, spark: SparkSession, database: str | None = None) -> None:
        self.spark = spark
        self.database = database or os.environ.get("U4CAST_DATABASE", DEFAULT_DATABASE)
        if not re.fullmatch(r"[A-Za-z0-9_]+", self.database):
            raise ValueError(
                f"The database name can hold letters, digits and _ only; got {self.database}."
            )

    # Setting up

    def create_tables(self) -> None:
        """Create the database and the forecasts and datasets tables, if they are missing."""
        self.spark.sql(f"create database if not exists {_quoted(self.database)}")
        for table, columns in (
            ("forecasts", FORECASTS_TABLE),
            ("datasets", DATASETS_TABLE),
            ("schedules", SCHEDULES_TABLE),
            ("production_runs", RUNS_TABLE),
        ):
            self.ensure_table(table, columns)

    # Forecasts in production

    def save_schedule(self, schedule: Schedule) -> None:
        """Save the schedule of a forecast, in place of the one it had."""
        self.spark.sql(
            f"delete from {_table(self.database, 'schedules')} "
            "where dataset = :dataset and forecast_name = :name",
            args={"dataset": schedule.dataset, "name": schedule.forecast_name},
        )
        self._append("schedules", pd.DataFrame([schedule.to_row()]), SCHEDULES_TABLE)

    def schedules(self) -> list[Schedule]:
        rows = self.spark.table(_table(self.database, "schedules")).toPandas()
        return [Schedule.from_row(row) for row in rows.to_dict("records")]

    def log_run(self, run: Mapping[str, Any]) -> None:
        """Append what one run of a schedule did."""
        row = {name: run.get(name) for name, _ in RUNS_TABLE}
        self._append("production_runs", pd.DataFrame([row]), RUNS_TABLE)

    def runs(self, dataset: str, forecast_name: str, limit: int = 200) -> list[dict[str, Any]]:
        """Return the runs of a schedule, newest first."""
        from pyspark.sql import functions as fn

        frame = (
            self.spark.table(_table(self.database, "production_runs"))
            .where((fn.col("dataset") == dataset) & (fn.col("forecast_name") == forecast_name))
            .orderBy(fn.col("ran_at").desc())
            .limit(limit)
        )
        return frame.toPandas().to_dict("records")

    def ensure_table(self, table: str, columns: Sequence[tuple[str, str]]) -> None:
        """Create a table if it is missing, and add any column it does not have yet.

        A table made by an earlier version of this project gets the columns
        added since. Rows already in it have no value in the new columns.
        """
        name = _table(self.database, table)
        listed = ", ".join(f"{_quoted(column)} {kind}" for column, kind in columns)
        self.spark.sql(f"create table if not exists {name} ({listed}) using iceberg")
        present = {field.name for field in self.spark.table(name).schema.fields}
        for column, kind in columns:
            if column not in present:
                self.spark.sql(f"alter table {name} add column {_quoted(column)} {kind}")
                logger.info("Added the column %s to %s", column, name)

    def _append(self, table: str, rows: pd.DataFrame, columns: Sequence[tuple[str, str]]) -> None:
        schema = ", ".join(f"{_quoted(name)} {kind}" for name, kind in columns)
        frame = self.spark.createDataFrame(rows[[name for name, _ in columns]], schema=schema)
        frame.writeTo(_table(self.database, table)).append()

    # Datasets

    def save_dataset(self, record: DatasetRecord, replace: bool = False) -> None:
        """Save a dataset. Its name must not be in use, unless `replace` is set.

        With `replace`, a saved dataset of the same name is replaced. Forecasts
        already made from it are not touched.
        """
        if any(saved.dataset == record.dataset for saved in self.datasets()):
            if not replace:
                raise ValueError(f"A dataset named {record.dataset} is already saved.")
            self.spark.sql(
                f"delete from {_table(self.database, 'datasets')} where dataset = :name",
                args={"name": record.dataset},
            )
        self._append("datasets", pd.DataFrame([record.to_row()]), DATASETS_TABLE)
        logger.info("Saved the dataset %s on %s", record.dataset, record.source_table)

    def datasets(self) -> list[DatasetRecord]:
        """Return the saved datasets, by name."""
        rows = self.spark.table(_table(self.database, "datasets")).toPandas()
        records = [DatasetRecord.from_row(row) for row in rows.to_dict("records")]
        return sorted(records, key=lambda record: record.dataset)

    def dataset(self, name: str) -> DatasetRecord:
        """Return the saved dataset of that name."""
        for record in self.datasets():
            if record.dataset == name:
                return record
        raise KeyError(f"No dataset named {name} is saved.")

    # History, read only

    def columns(self, source_table: str) -> list[tuple[str, str]]:
        """Return the name and type of each column of a table."""
        fields = self.spark.table(_source(source_table)).schema.fields
        return [(field.name, field.dataType.simpleString()) for field in fields]

    def dimension_values(self, source_table: str, column: str, limit: int = 10_000) -> list[Any]:
        """Return the distinct values of a column, sorted, for the user to filter on."""
        frame = self.spark.table(_source(source_table)).select(column).distinct()
        values = [row[0] for row in frame.limit(limit).collect()]
        return sorted(values, key=lambda value: (value is None, str(value)))

    def date_range(self, source_table: str, date_column: str) -> tuple[dt.date, dt.date]:
        """Return the first and last date of a table."""
        from pyspark.sql import functions as fn

        row = (
            self.spark.table(_source(source_table))
            .agg(fn.min(date_column), fn.max(date_column))
            .collect()[0]
        )
        return pd.Timestamp(row[0]).date(), pd.Timestamp(row[1]).date()

    def _slice(
        self,
        source_table: str,
        date_column: str,
        slice_filter: Mapping[str, Sequence[Any]] | None,
        history_start: object | None,
        history_end: object | None,
    ) -> DataFrame:
        from pyspark.sql import functions as fn

        frame = self.spark.table(_source(source_table))
        for column, values in (slice_filter or {}).items():
            frame = frame.where(fn.col(column).isin(list(values)))
        if history_start is not None:
            frame = frame.where(fn.col(date_column) >= fn.lit(pd.Timestamp(history_start).date()))
        if history_end is not None:
            frame = frame.where(fn.col(date_column) <= fn.lit(pd.Timestamp(history_end).date()))
        return frame

    def load_slice(
        self,
        source_table: str,
        *,
        date_column: str,
        target_column: str,
        dimension_columns: Sequence[str],
        slice_filter: Mapping[str, Sequence[Any]] | None = None,
        history_start: object | None = None,
        history_end: object | None = None,
    ) -> pd.DataFrame:
        """Load one slice of history: filtered, summed and limited in the query.

        The result has one row per date and combination of the chosen
        dimension columns, with the target summed over everything else. It is
        what `u4cast.prepare.prepare` takes.
        """
        from pyspark.sql import functions as fn

        keys = [date_column, *dimension_columns]
        frame = (
            self._slice(source_table, date_column, slice_filter, history_start, history_end)
            .groupBy(*keys)
            .agg(fn.sum(target_column).cast("double").alias(target_column))
        )
        result = frame.toPandas()
        result[date_column] = pd.to_datetime(result[date_column])
        logger.info("Loaded a slice of %d rows from %s", len(result), source_table)
        return result

    # Forecasts

    def save_forecast(self, issued: IssuedForecast, dataset: DatasetRecord) -> None:
        """Append a forecast: its values first, then its row in the forecasts table.

        The values table has all the dimension columns of the dataset, as
        text. A forecast summed up to fewer columns leaves the others empty.
        """
        table = forecast_values_table(dataset.dataset)
        columns = values_table_schema(dataset.dimension_columns)
        self.ensure_table(table, columns)
        values = issued.values.copy()
        for name in dataset.dimension_columns:
            if name in values.columns:
                values[name] = (
                    values[name].astype("string").astype(object).where(values[name].notna(), None)
                )
            else:
                values[name] = None
        values["date"] = pd.to_datetime(values["date"]).dt.date
        values["step"] = values["step"].astype("int32")
        self._append(table, values, columns)
        self._append("forecasts", pd.DataFrame([issued.record.to_row()]), FORECASTS_TABLE)
        logger.info(
            "Stored %s %s: %d rows",
            issued.record.kind.value,
            issued.record.forecast_id,
            len(values),
        )

    def forecasts(
        self,
        dataset: str | None = None,
        forecast_name: str | None = None,
        kind: Kind | None = None,
    ) -> list[ForecastRecord]:
        """Return stored forecasts, newest history first, narrowed by what is given."""
        from pyspark.sql import functions as fn

        frame = self.spark.table(_table(self.database, "forecasts"))
        for column, value in (
            ("dataset", dataset),
            ("forecast_name", forecast_name),
            ("kind", kind.value if kind else None),
        ):
            if value is not None:
                frame = frame.where(fn.col(column) == value)
        records = [ForecastRecord.from_row(row) for row in frame.toPandas().to_dict("records")]
        return sorted(
            records, key=lambda record: (record.history_end_date, record.created_at), reverse=True
        )

    def forecast_sizes(self, dataset: str) -> dict[str, int]:
        """Return how many series each stored forecast of a dataset has."""
        from pyspark.sql import functions as fn

        table = forecast_values_table(dataset)
        if not self.spark.catalog.tableExists(f"{self.database}.{table}"):
            return {}
        rows = (
            self.spark.table(_table(self.database, table))
            .groupBy("forecast_id")
            .agg(fn.countDistinct(TS_ID))
            .collect()
        )
        return {row[0]: int(row[1]) for row in rows}

    def forecast_values(self, dataset: str, forecast_ids: Sequence[str]) -> pd.DataFrame:
        """Return the stored values of the given forecasts of a dataset."""
        from pyspark.sql import functions as fn

        frame = self.spark.table(_table(self.database, forecast_values_table(dataset)))
        result = frame.where(fn.col("forecast_id").isin(list(forecast_ids))).toPandas()
        result["date"] = pd.to_datetime(result["date"])
        return result.sort_values(["forecast_id", TS_ID, "step"], ignore_index=True)

    # Looking at tables, for the screens that add a dataset

    def tables(self) -> list[str]:
        """Return the full names of the history tables in this database.

        The tables the application writes forecasts and datasets to are left out.
        """
        rows = self.spark.sql(f"show tables in {_quoted(self.database)}").collect()
        names = sorted(row["tableName"] for row in rows)
        return [
            f"{self.database}.{name}"
            for name in names
            if name not in OWN_TABLES and not name.startswith("forecast_values_")
        ]

    def row_count(self, source_table: str) -> int:
        """Return the number of rows of a table."""
        return self.spark.table(_source(source_table)).count()

    def preview(self, source_table: str, limit: int = 20) -> pd.DataFrame:
        """Return the first rows of a table, as stored."""
        return self.spark.table(_source(source_table)).limit(limit).toPandas()

    def distinct_dates(self, source_table: str, date_column: str, limit: int = 50_000) -> list[Any]:
        """Return the distinct values of a date column, for detecting the frequency."""
        frame = self.spark.table(_source(source_table)).select(date_column).distinct()
        return [row[0] for row in frame.limit(limit).collect()]

    def count_slice_rows(
        self,
        source_table: str,
        *,
        date_column: str,
        slice_filter: Mapping[str, Sequence[Any]] | None = None,
        history_start: object | None = None,
        history_end: object | None = None,
    ) -> int:
        """Return how many rows of history a slice reads, before any summing.

        It is an upper limit on the rows `load_slice` returns, and cheap, so
        a slice that is too large can be refused before it is loaded.
        """
        return self._slice(
            source_table, date_column, slice_filter, history_start, history_end
        ).count()

    # Uploads

    def create_table_from_frame(self, table: str, frame: pd.DataFrame) -> str:
        """Store an uploaded file as a new table and return its full name.

        This is the only way the application creates a history table. It
        refuses a name that exists, so it never changes history that is there.
        """
        if not re.fullmatch(r"[A-Za-z0-9_]+", table):
            raise ValueError(f"A table name can hold letters, digits and _ only; got {table}.")
        if self.spark.catalog.tableExists(f"{self.database}.{table}"):
            raise ValueError(f"A table named {table} already exists.")
        self.spark.createDataFrame(frame).writeTo(_table(self.database, table)).using(
            "iceberg"
        ).create()
        logger.info("Created the table %s.%s with %d rows", self.database, table, len(frame))
        return f"{self.database}.{table}"
