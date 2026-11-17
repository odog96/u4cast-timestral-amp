"""Data preparation: ts_id, the date grid, the array of series, and series left out."""

import numpy as np
import pandas as pd
import pytest

from conftest import long_frame, prepare_units
from u4cast.frequency import Frequency
from u4cast.prepare import Fill, LeftOutReason, make_ts_ids, prepare

MONTHS = pd.date_range("2023-01-01", periods=30, freq="MS")


def test_ts_id_is_built_from_the_dimension_values():
    frame = pd.DataFrame({"store": [2633, 2633], "category": ["VODKA", "RUM"]})
    assert make_ts_ids(frame, ["store", "category"]).tolist() == [
        "store=2633|category=VODKA",
        "store=2633|category=RUM",
    ]


def test_ts_id_does_not_depend_on_row_order_or_on_other_series():
    frame = pd.DataFrame(
        {"store": [1, 2, 3, 1, 2], "category": ["A", "B", "C", "B", "A"], "units": range(5)}
    )
    ids = dict(zip(frame["units"], make_ts_ids(frame, ["store", "category"]), strict=True))
    shuffled = frame.sample(frac=1, random_state=3)
    fewer = frame[frame["store"] != 1]
    for other in (shuffled, fewer):
        other_ids = dict(
            zip(other["units"], make_ts_ids(other, ["store", "category"]), strict=True)
        )
        assert all(ids[row] == ts_id for row, ts_id in other_ids.items())


def test_ts_id_keeps_values_that_contain_the_separator_apart():
    frame = pd.DataFrame({"a": ["x|b=y", "x"], "b": ["z", "y|b=z"]})
    first, second = make_ts_ids(frame, ["a", "b"])
    assert first != second


def test_ts_id_is_the_same_whether_whole_numbers_load_as_integers_or_floats():
    as_integers = pd.DataFrame({"store": [2633, 2634]})
    as_floats = pd.DataFrame({"store": [2633.0, 2634.0, np.nan]})
    assert make_ts_ids(as_integers, ["store"]).tolist() == ["store=2633", "store=2634"]
    assert make_ts_ids(as_floats, ["store"]).tolist() == ["store=2633", "store=2634", "store="]


def test_a_dataset_without_dimension_columns_is_one_series():
    frame = pd.DataFrame({"date": MONTHS, "units": np.arange(30.0)})
    panel = prepare(
        frame,
        date_column="date",
        target_column="units",
        dimension_columns=[],
        frequency=Frequency.MONTHLY,
    )
    assert panel.ts_ids == ["total"]
    assert panel.values.shape == (1, 30)


def test_array_has_one_row_per_series_and_one_column_per_date():
    frame = long_frame(MONTHS, ["B", "A", "C"]).sample(frac=1, random_state=1)
    panel = prepare_units(frame, Frequency.MONTHLY)
    assert panel.values.shape == (3, 30)
    assert panel.ts_ids == ["sku=A", "sku=B", "sku=C"]
    assert panel.series.columns.tolist() == ["ts_id", "sku"]
    assert panel.dates.equals(MONTHS)
    # long_frame gives series s the value 100 * (s + 1) + i at date i; A was second.
    assert panel.values[0].tolist() == [200.0 + i for i in range(30)]
    assert panel.values[1].tolist() == [100.0 + i for i in range(30)]
    assert len(panel.left_out) == 0


def test_a_date_with_no_data_becomes_nan_not_zero():
    frame = long_frame(MONTHS, ["A", "B"])
    frame = frame[~((frame["sku"] == "A") & (frame["date"] == MONTHS[7]))]
    frame.loc[(frame["sku"] == "B") & (frame["date"] == MONTHS[9]), "units"] = np.nan
    panel = prepare_units(frame, Frequency.MONTHLY)
    assert np.isnan(panel.values[0, 7])
    assert np.isnan(panel.values[1, 9])
    assert int(np.isnan(panel.values).sum()) == 2
    assert panel.n_series == 2


def test_rows_of_the_same_series_and_date_are_summed():
    frame = long_frame(MONTHS, ["A"])
    extra = pd.DataFrame({"date": [MONTHS[4]], "sku": ["A"], "units": [1000.0]})
    panel = prepare_units(pd.concat([frame, extra]), Frequency.MONTHLY)
    assert panel.values[0, 4] == 104.0 + 1000.0
    assert panel.values[0, 5] == 105.0


def test_history_window_limits_the_dates_used():
    panel = prepare_units(
        long_frame(MONTHS, ["A", "B"]),
        Frequency.MONTHLY,
        history_start="2023-04-01",
        history_end="2025-05-15",
    )
    assert panel.dates[0] == pd.Timestamp("2023-04-01")
    assert panel.dates[-1] == pd.Timestamp("2025-05-01")
    assert panel.values.shape == (2, 26)
    assert panel.values[0, 0] == 103.0


def test_series_can_start_at_different_dates():
    frame = long_frame(MONTHS, ["A", "LATE", "EARLY"])
    frame = frame[~((frame["sku"] == "LATE") & (frame["date"] < MONTHS[3]))]
    frame = frame[~((frame["sku"] == "EARLY") & (frame["date"] > MONTHS[25]))]
    panel = prepare_units(frame, Frequency.MONTHLY)
    assert panel.ts_ids == ["sku=A", "sku=EARLY", "sku=LATE"]
    assert panel.first_steps.tolist() == [0, 0, 3]
    assert len(panel.left_out) == 0
    # LATE is missing before its first value and complete from there on.
    assert np.isnan(panel.values[2, :3]).all()
    assert not np.isnan(panel.values[2, 3:]).any()
    # EARLY has no row after month 25; by default those dates stay missing.
    assert np.isnan(panel.values[1, 26:]).all()


def test_a_series_needs_the_minimum_history_from_its_first_value():
    frame = long_frame(MONTHS, ["A", "ENOUGH", "SHORT"])  # 30 months; the minimum is 24
    frame = frame[~((frame["sku"] == "ENOUGH") & (frame["date"] < MONTHS[6]))]
    frame = frame[~((frame["sku"] == "SHORT") & (frame["date"] < MONTHS[7]))]
    panel = prepare_units(frame, Frequency.MONTHLY)
    assert panel.ts_ids == ["sku=A", "sku=ENOUGH"]
    (left_out,) = panel.left_out.to_dict("records")
    assert left_out["ts_id"] == "sku=SHORT"
    assert left_out["reason"] == LeftOutReason.TOO_SHORT
    assert left_out["first_date"] == MONTHS[7]
    assert left_out["last_date"] == MONTHS[29]
    assert left_out["steps"] == 23
    assert left_out["sku"] == "SHORT"


def gappy_frame() -> pd.DataFrame:
    """One series that starts at month 2, with values 10, 20, 40 at months 2, 3, 6."""
    frame = long_frame(MONTHS, ["FULL", "GAPPY"])
    gappy = frame["sku"] == "GAPPY"
    frame = frame[~gappy | frame["date"].isin(MONTHS[[2, 3, 6]])].copy()
    frame.loc[frame["sku"] == "GAPPY", "units"] = [10.0, 20.0, 40.0]
    return frame


@pytest.mark.parametrize(
    ("fill", "months_2_to_8"),
    [
        (Fill.MISSING, [10.0, 20.0, None, None, 40.0, None, None]),
        (Fill.ZERO, [10.0, 20.0, 0.0, 0.0, 40.0, 0.0, 0.0]),
        (Fill.AVERAGE, [10.0, 20.0, 70 / 3, 70 / 3, 40.0, 70 / 3, 70 / 3]),
        (Fill.INTERPOLATE, [10.0, 20.0, 80 / 3, 100 / 3, 40.0, 40.0, 40.0]),
        (Fill.PREVIOUS, [10.0, 20.0, 20.0, 20.0, 40.0, 40.0, 40.0]),
    ],
)
def test_the_user_chooses_what_a_date_with_no_row_becomes(fill, months_2_to_8):
    panel = prepare_units(gappy_frame(), Frequency.MONTHLY, fill=fill)
    assert panel.fill is fill
    gappy = panel.values[panel.ts_ids.index("sku=GAPPY")]
    expected = np.array(months_2_to_8, dtype=float)
    assert np.allclose(gappy[2:9], expected, equal_nan=True)
    # Before its first value a series is missing, whatever the choice.
    assert np.isnan(gappy[:2]).all()
    # A series with a row on every date is not touched.
    full = panel.values[panel.ts_ids.index("sku=FULL")]
    assert full.tolist() == [100.0 + i for i in range(30)]


def test_filling_for_a_past_cutoff_uses_nothing_from_after_it():
    panel = prepare_units(gappy_frame(), Frequency.MONTHLY, fill=Fill.AVERAGE)
    gappy = panel.ts_ids.index("sku=GAPPY")
    # Up to month 5 only 10 and 20 are known, so the average is 15, not 70 / 3.
    assert panel.history(6)[gappy, 2:].tolist() == [10.0, 20.0, 15.0, 15.0]
    line = prepare_units(gappy_frame(), Frequency.MONTHLY, fill=Fill.INTERPOLATE)
    # The straight line to month 6 is not known yet: the last value is repeated.
    assert line.history(6)[gappy, 2:].tolist() == [10.0, 20.0, 20.0, 20.0]
    assert line.history(30) is line.values


def test_a_date_with_no_row_is_an_actual_only_when_it_means_zero():
    zero = prepare_units(gappy_frame(), Frequency.MONTHLY, fill=Fill.ZERO)
    gappy = zero.ts_ids.index("sku=GAPPY")
    assert zero.actuals[gappy, 4] == 0.0
    for fill in (Fill.MISSING, Fill.AVERAGE, Fill.INTERPOLATE, Fill.PREVIOUS):
        panel = prepare_units(gappy_frame(), Frequency.MONTHLY, fill=fill)
        assert np.isnan(panel.actuals[gappy, 4])
        assert panel.actuals[gappy, 6] == 40.0


def test_a_series_with_no_values_is_left_out_and_listed():
    frame = long_frame(MONTHS, ["A", "EMPTY"])
    frame.loc[frame["sku"] == "EMPTY", "units"] = np.nan
    panel = prepare_units(frame, Frequency.MONTHLY)
    assert panel.ts_ids == ["sku=A"]
    assert panel.left_out["reason"].tolist() == [LeftOutReason.NO_VALUES]
    assert panel.left_out["steps"].tolist() == [0]
    assert panel.left_out["first_date"].isna().all()


def test_a_window_shorter_than_the_minimum_history_leaves_every_series_out():
    panel = prepare_units(long_frame(MONTHS[:23], ["A", "B"]), Frequency.MONTHLY)
    assert panel.n_series == 0
    assert panel.values.shape == (0, 23)
    assert panel.left_out["reason"].tolist() == [LeftOutReason.TOO_SHORT] * 2
    assert panel.minimum_history == 24


def test_two_years_of_monthly_history_is_enough():
    panel = prepare_units(long_frame(MONTHS[:24], ["A"]), Frequency.MONTHLY)
    assert panel.n_series == 1


def test_weekly_history_under_two_years_needs_the_override():
    weeks = pd.date_range("2024-01-01", periods=40, freq="W-MON")
    frame = long_frame(weeks, ["A"])
    assert prepare_units(frame, Frequency.WEEKLY).n_series == 0
    overridden = prepare_units(frame, Frequency.WEEKLY, minimum_history_override=32)
    assert overridden.n_series == 1
    assert overridden.minimum_history == 32


def test_rows_with_dates_off_the_grid_are_dropped_and_counted():
    frame = long_frame(MONTHS, ["A"])
    stray = pd.DataFrame(
        {"date": [pd.Timestamp("2023-03-17"), pd.NaT], "sku": ["A", "A"], "units": [5.0, 6.0]}
    )
    panel = prepare_units(pd.concat([frame, stray]), Frequency.MONTHLY)
    assert panel.rows_off_grid == 2
    assert panel.values[0, 2] == 102.0


def test_the_data_passed_in_is_not_changed():
    frame = long_frame(MONTHS, ["A", "B"]).sample(frac=1, random_state=2)
    before = frame.copy(deep=True)
    prepare_units(frame, Frequency.MONTHLY)
    pd.testing.assert_frame_equal(frame, before)


def test_text_dates_and_whole_number_targets_are_accepted():
    frame = long_frame(MONTHS, ["A"])
    frame["date"] = frame["date"].dt.strftime("%Y-%m-%d")
    frame["units"] = frame["units"].astype(int)
    panel = prepare_units(frame, Frequency.MONTHLY)
    assert panel.values.dtype == np.float64
    assert panel.values[0, 3] == 103.0


def test_times_of_day_and_time_zones_on_dates_are_ignored():
    frame = long_frame(MONTHS, ["A"])
    frame["date"] = (frame["date"] + pd.Timedelta(hours=9)).dt.tz_localize("UTC")
    panel = prepare_units(frame, Frequency.MONTHLY)
    assert panel.dates.equals(MONTHS)
    assert panel.n_series == 1


def test_unusable_input_is_refused_with_a_clear_message():
    frame = long_frame(MONTHS, ["A"])
    with pytest.raises(ValueError, match="no column named price"):
        prepare(
            frame,
            date_column="date",
            target_column="price",
            dimension_columns=["sku"],
            frequency=Frequency.MONTHLY,
        )
    with pytest.raises(ValueError, match="different columns"):
        prepare(
            frame,
            date_column="date",
            target_column="units",
            dimension_columns=["sku", "sku"],
            frequency=Frequency.MONTHLY,
        )
    with pytest.raises(ValueError, match="no values"):
        prepare_units(frame, Frequency.MONTHLY, history_start="2030-01-01")
    with pytest.raises(ValueError, match="units must hold numbers"):
        prepare_units(frame.assign(units="many"), Frequency.MONTHLY)
