"""Error measures that compare forecasts with what actually happened.

- MAE: the average absolute error.
- RMSE: the square root of the average squared error.
- MAPE: the average of the absolute error divided by the actual. It leaves
  out dates whose actual is zero, and is missing when every actual is zero.
- WAPE: the sum of absolute errors divided by the sum of actuals. It works
  when some actuals are zero.

MAPE and WAPE are ratios: 0.138 means 13.8%. A date with no actual, or no
forecast, is not scored.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

METRICS: tuple[str, ...] = ("mae", "rmse", "mape", "wape")


def error_metrics_by(
    groups: np.ndarray | pd.Series, actual: np.ndarray, forecast: np.ndarray
) -> pd.DataFrame:
    """Return the error measures of each group, for example of each series.

    The result has one row per group, indexed by group, with the columns `n`
    (dates scored), `mae`, `rmse`, `mape` and `wape`. A group with no scored
    date has `n` of 0 and missing measures.
    """
    actual = np.asarray(actual, dtype=np.float64)
    forecast = np.asarray(forecast, dtype=np.float64)
    scored = ~np.isnan(actual) & ~np.isnan(forecast)
    error = np.where(scored, np.abs(forecast - actual), np.nan)
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = np.where(scored & (actual != 0), error / np.abs(actual), np.nan)
    sums = (
        pd.DataFrame(
            {
                "group": np.asarray(groups),
                "n": scored.astype(np.int64),
                "error": np.where(scored, error, 0.0),
                "squared": np.where(scored, error**2, 0.0),
                "actual": np.where(scored, np.abs(actual), 0.0),
                "ratio": np.nan_to_num(ratio, nan=0.0),
                "ratios": (~np.isnan(ratio)).astype(np.int64),
            }
        )
        .groupby("group", sort=True)
        .sum()
    )
    n = sums["n"].where(sums["n"] > 0)
    return pd.DataFrame(
        {
            "n": sums["n"],
            "mae": sums["error"] / n,
            "rmse": np.sqrt(sums["squared"] / n),
            "mape": sums["ratio"] / sums["ratios"].where(sums["ratios"] > 0),
            "wape": sums["error"] / sums["actual"].where(sums["actual"] > 0),
        }
    )


def error_metrics(actual: np.ndarray, forecast: np.ndarray) -> dict[str, float]:
    """Return `n`, `mae`, `rmse`, `mape` and `wape` over all the given dates together."""
    actual = np.asarray(actual, dtype=np.float64).ravel()
    forecast = np.asarray(forecast, dtype=np.float64).ravel()
    row = error_metrics_by(np.zeros(len(actual), dtype=np.int64), actual, forecast)
    if len(row) == 0:
        return {"n": 0, **{name: float("nan") for name in METRICS}}
    return {"n": int(row["n"].iloc[0]), **{name: float(row[name].iloc[0]) for name in METRICS}}
