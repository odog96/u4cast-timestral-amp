"""Shared helpers for the tests. No test needs the Timestral weights by default."""

from __future__ import annotations

from collections.abc import Callable

import pandas as pd
import pytest

from u4cast.frequency import Frequency
from u4cast.prepare import Panel, prepare


def long_frame(
    dates: pd.DatetimeIndex,
    series: list[str],
    value: Callable[[int, int], float] = lambda s, i: 100.0 * (s + 1) + i,
) -> pd.DataFrame:
    """One row per date and series: columns `date`, `sku` and `units`."""
    return pd.DataFrame(
        [
            {"date": date, "sku": sku, "units": value(s, i)}
            for s, sku in enumerate(series)
            for i, date in enumerate(dates)
        ]
    )


def prepare_units(frame: pd.DataFrame, frequency: Frequency, **options: object) -> Panel:
    return prepare(
        frame,
        date_column="date",
        target_column="units",
        dimension_columns=["sku"],
        frequency=frequency,
        **options,
    )


@pytest.fixture
def monthly_panel() -> Panel:
    """Three SKUs with 36 months of history, January 2023 to December 2025."""
    dates = pd.date_range("2023-01-01", periods=36, freq="MS")
    return prepare_units(long_frame(dates, ["A", "B", "C"]), Frequency.MONTHLY)
