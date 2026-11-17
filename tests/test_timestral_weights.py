"""The real Timestral model, in the same process. Skipped unless asked for.

Run with `U4CAST_TEST_WEIGHTS=1 pytest -m weights`. It needs the `model`
extra and downloads the weights on first use. It uses about 2 GB of memory.
"""

import os

import numpy as np
import pandas as pd
import pytest

from conftest import long_frame, prepare_units
from u4cast.forecast import issue_forecast
from u4cast.frequency import Frequency
from u4cast.model import InProcessForecaster

pytestmark = [
    pytest.mark.weights,
    pytest.mark.skipif(
        os.environ.get("U4CAST_TEST_WEIGHTS") != "1", reason="set U4CAST_TEST_WEIGHTS=1 to run"
    ),
]


def test_one_forecast_of_100_series_of_32_steps():
    pytest.importorskip("timestral")
    days = pd.date_range("2025-01-01", periods=32, freq="D")
    skus = [f"SKU{n:03d}" for n in range(100)]
    frame = long_frame(days, skus, value=lambda s, i: 50.0 + s + 10.0 * (i % 7))
    frame.loc[(frame["sku"] == "SKU007") & (frame["date"] == days[10]), "units"] = np.nan
    panel = prepare_units(frame, Frequency.DAILY)
    model = InProcessForecaster(batch_size=60)

    issued = issue_forecast(
        panel, model, horizon=14, forecast_name="check", dataset="synthetic", target_column="units"
    )

    values = issued.values
    assert len(values) == 100 * 14
    assert issued.record.model_name == "mistralai/Timestral-209M"
    assert np.isfinite(values[["forecast", "low", "high"]].to_numpy()).all()
    assert (values["low"] <= values["forecast"]).all()
    assert (values["forecast"] <= values["high"]).all()
    assert (values["low"] >= 0).all()
    # The weekly pattern is easy: the forecast should be close to repeating it.
    week_earlier = np.tile(panel.values[:, -7:], 2).ravel()
    assert np.abs(values["forecast"].to_numpy() - week_earlier).mean() < 5.0
