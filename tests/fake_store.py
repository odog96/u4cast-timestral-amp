"""A stand-in for `u4cast.storage.Store` that keeps everything in memory.

It answers the same calls the web server makes, with pandas in place of Spark,
so the web server's tests need no data lake.
"""

from __future__ import annotations

import pandas as pd

from u4cast.forecast import IssuedForecast
from u4cast.layout import DatasetRecord, ForecastRecord, Kind

TYPES = {"M": "date", "O": "string", "f": "double", "i": "bigint", "b": "boolean"}


class FakeStore:
    database = "fake"

    def __init__(self) -> None:
        self.frames: dict[str, pd.DataFrame] = {}
        self.saved_datasets: list[DatasetRecord] = []
        self.saved_forecasts: list[ForecastRecord] = []
        self.saved_values: list[pd.DataFrame] = []
        self.loads = 0
        self.saved_schedules: dict = {}
        self.saved_runs: list[dict] = []

    def add_table(self, name: str, frame: pd.DataFrame) -> str:
        self.frames[f"{self.database}.{name}"] = frame.reset_index(drop=True)
        return f"{self.database}.{name}"

    def create_tables(self) -> None:
        pass

    def save_schedule(self, schedule) -> None:
        self.saved_schedules[(schedule.dataset, schedule.forecast_name)] = schedule

    def schedules(self) -> list:
        return list(self.saved_schedules.values())

    def log_run(self, run) -> None:
        self.saved_runs.append(dict(run))

    def runs(self, dataset: str, forecast_name: str, limit: int = 200) -> list[dict]:
        mine = [
            r
            for r in self.saved_runs
            if (r["dataset"], r["forecast_name"]) == (dataset, forecast_name)
        ]
        return sorted(mine, key=lambda r: r["ran_at"], reverse=True)[:limit]

    def tables(self) -> list[str]:
        return sorted(self.frames)

    def columns(self, table: str) -> list[tuple[str, str]]:
        frame = self.frames[table]
        return [(name, TYPES.get(frame[name].dtype.kind, "string")) for name in frame.columns]

    def preview(self, table: str, limit: int = 20) -> pd.DataFrame:
        return self.frames[table].head(limit)

    def row_count(self, table: str) -> int:
        return len(self.frames[table])

    def distinct_dates(self, table: str, date_column: str, limit: int = 50_000) -> list:
        return list(self.frames[table][date_column].drop_duplicates())[:limit]

    def dimension_values(self, table: str, column: str, limit: int = 10_000) -> list:
        return sorted(self.frames[table][column].dropna().unique().tolist())[:limit]

    def _slice(self, table, date_column, slice_filter, history_start, history_end) -> pd.DataFrame:
        frame = self.frames[table]
        for column, values in (slice_filter or {}).items():
            frame = frame[frame[column].isin(list(values))]
        days = pd.to_datetime(frame[date_column])
        if history_start is not None:
            frame = frame[days >= pd.Timestamp(history_start)]
        if history_end is not None:
            frame = frame[pd.to_datetime(frame[date_column]) <= pd.Timestamp(history_end)]
        return frame

    def count_slice_rows(
        self, table, *, date_column, slice_filter=None, history_start=None, history_end=None
    ) -> int:
        return len(self._slice(table, date_column, slice_filter, history_start, history_end))

    def load_slice(
        self,
        table,
        *,
        date_column,
        target_column,
        dimension_columns,
        slice_filter=None,
        history_start=None,
        history_end=None,
    ) -> pd.DataFrame:
        self.loads += 1
        frame = self._slice(table, date_column, slice_filter, history_start, history_end)
        keys = [date_column, *dimension_columns]
        result = frame.groupby(keys, as_index=False, dropna=False)[target_column].sum(min_count=1)
        result[date_column] = pd.to_datetime(result[date_column])
        return result

    def datasets(self) -> list[DatasetRecord]:
        return sorted(self.saved_datasets, key=lambda record: record.dataset)

    def save_dataset(self, record: DatasetRecord, replace: bool = False) -> None:
        if any(saved.dataset == record.dataset for saved in self.saved_datasets) and not replace:
            raise ValueError(f"A dataset named {record.dataset} is already saved.")
        self.saved_datasets = [s for s in self.saved_datasets if s.dataset != record.dataset]
        self.saved_datasets.append(record)

    def save_forecast(self, issued: IssuedForecast, dataset: DatasetRecord) -> None:
        values = issued.values.copy()
        for column in dataset.dimension_columns:
            values[column] = values[column].astype(str) if column in values.columns else None
        self.saved_values.append(values.assign(dataset=dataset.dataset))
        self.saved_forecasts.append(issued.record)

    def forecasts(self, dataset=None, forecast_name=None, kind: Kind | None = None) -> list:
        return [
            record
            for record in self.saved_forecasts
            if (dataset is None or record.dataset == dataset)
            and (forecast_name is None or record.forecast_name == forecast_name)
            and (kind is None or record.kind is kind)
        ]

    def forecast_values(self, dataset: str, forecast_ids) -> pd.DataFrame:
        stored = pd.concat(self.saved_values, ignore_index=True)
        chosen = stored[
            (stored["dataset"] == dataset) & stored["forecast_id"].isin(list(forecast_ids))
        ]
        return chosen.drop(columns="dataset").reset_index(drop=True)

    def forecast_sizes(self, dataset: str) -> dict[str, int]:
        return {
            values["forecast_id"].iloc[0]: values["ts_id"].nunique()
            for values in self.saved_values
            if values["dataset"].iloc[0] == dataset
        }

    def create_table_from_frame(self, table: str, frame: pd.DataFrame) -> str:
        if f"{self.database}.{table}" in self.frames:
            raise ValueError(f"A table named {table} already exists.")
        return self.add_table(table, frame)
