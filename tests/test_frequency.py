"""Frequency detection, the date grid, future dates and the minimum history."""

import numpy as np
import pandas as pd
import pytest

from u4cast.frequency import Calendar, Frequency, detect_frequency, minimum_history


def days(index: pd.DatetimeIndex) -> list[str]:
    return list(index.strftime("%Y-%m-%d"))


@pytest.mark.parametrize(
    ("pandas_frequency", "expected"),
    [
        ("D", Frequency.DAILY),
        ("W-MON", Frequency.WEEKLY),
        ("W-SAT", Frequency.WEEKLY),
        ("MS", Frequency.MONTHLY),
        ("ME", Frequency.MONTHLY),
        ("QS", Frequency.QUARTERLY),
        ("QE", Frequency.QUARTERLY),
    ],
)
def test_detects_each_frequency(pandas_frequency, expected):
    guess = detect_frequency(pd.date_range("2023-01-01", periods=40, freq=pandas_frequency))
    assert guess.frequency is expected
    assert guess.share == 1.0


def test_detection_survives_missing_and_repeated_dates():
    dates = pd.date_range("2024-01-01", periods=60, freq="D").delete([5, 6, 20, 41])
    guess = detect_frequency(list(dates) + list(dates[:10]))
    assert guess.frequency is Frequency.DAILY
    assert 0.9 < guess.share < 1.0


def test_detection_gives_no_guess_when_it_cannot_tell():
    assert detect_frequency(["2024-01-01", "2024-01-02"]).frequency is None
    every_other_day = pd.date_range("2024-01-01", periods=30, freq="2D")
    assert detect_frequency(every_other_day).frequency is None


@pytest.mark.parametrize(
    ("frequency", "expected"),
    [
        (Frequency.DAILY, 32),
        (Frequency.WEEKLY, 104),
        (Frequency.MONTHLY, 24),
        (Frequency.QUARTERLY, 8),
    ],
)
def test_default_minimum_history(frequency, expected):
    assert minimum_history(frequency) == expected


def test_weekly_minimum_can_be_overridden_down_to_32():
    assert minimum_history(Frequency.WEEKLY, override=32) == 32
    assert minimum_history(Frequency.WEEKLY, override=60) == 60
    with pytest.raises(ValueError, match="below 32"):
        minimum_history(Frequency.WEEKLY, override=31)


def test_override_cannot_go_below_a_default_that_is_under_32():
    with pytest.raises(ValueError, match="below 24"):
        minimum_history(Frequency.MONTHLY, override=12)
    with pytest.raises(ValueError, match="below 8"):
        minimum_history(Frequency.QUARTERLY, override=4)
    with pytest.raises(ValueError, match="below 32"):
        minimum_history(Frequency.DAILY, override=14)


def test_daily_future_dates_cross_a_leap_day_and_a_year_end():
    calendar = Calendar(Frequency.DAILY, pd.Timestamp("2024-02-01"))
    assert days(calendar.future_dates(28, 3)) == ["2024-02-29", "2024-03-01", "2024-03-02"]
    calendar = Calendar(Frequency.DAILY, pd.Timestamp("2023-12-01"))
    assert days(calendar.future_dates(31, 2)) == ["2024-01-01", "2024-01-02"]


def test_weekly_future_dates_keep_the_weekday_of_the_data():
    saturdays = pd.date_range("2024-01-06", periods=10, freq="W-SAT")
    calendar = Calendar.from_dates(Frequency.WEEKLY, saturdays)
    future = calendar.future_dates(10, 4)
    assert days(future) == ["2024-03-16", "2024-03-23", "2024-03-30", "2024-04-06"]
    assert set(future.day_of_week) == {5}


def test_month_end_dates_stay_on_the_last_day_of_each_month():
    calendar = Calendar.from_dates(
        Frequency.MONTHLY, pd.date_range("2023-11-30", periods=3, freq="ME")
    )
    assert calendar.month_end
    assert days(calendar.future_dates(3, 4)) == [
        "2024-02-29",
        "2024-03-31",
        "2024-04-30",
        "2024-05-31",
    ]


def test_month_start_and_mid_month_dates_keep_their_day():
    starts = Calendar.from_dates(
        Frequency.MONTHLY, pd.date_range("2024-10-01", periods=3, freq="MS")
    )
    assert days(starts.future_dates(3, 2)) == ["2025-01-01", "2025-02-01"]
    mid_month = Calendar(Frequency.MONTHLY, pd.Timestamp("2024-01-30"))
    assert days(mid_month.dates(0, 4)) == ["2024-01-30", "2024-02-29", "2024-03-30", "2024-04-30"]


def test_quarterly_future_dates_keep_the_quarter_months_of_the_data():
    fiscal = pd.date_range("2022-02-01", periods=8, freq="QS-FEB")
    calendar = Calendar.from_dates(Frequency.QUARTERLY, fiscal)
    assert days(calendar.future_dates(8, 3)) == ["2024-02-01", "2024-05-01", "2024-08-01"]
    quarter_ends = pd.date_range("2022-03-31", periods=4, freq="QE")
    calendar = Calendar.from_dates(Frequency.QUARTERLY, quarter_ends)
    assert days(calendar.future_dates(4, 2)) == ["2023-03-31", "2023-06-30"]


def test_grid_follows_the_usual_weekday_not_a_stray_first_date():
    mondays = pd.date_range("2024-01-08", periods=10, freq="W-MON")
    calendar = Calendar.from_dates(Frequency.WEEKLY, [pd.Timestamp("2024-01-03"), *mondays])
    assert calendar.anchor == pd.Timestamp("2024-01-08")


def test_steps_of_marks_dates_that_are_not_on_the_grid():
    calendar = Calendar(Frequency.MONTHLY, pd.Timestamp("2024-01-01"))
    steps = calendar.steps_of(["2024-01-01", "2024-03-01", "2024-03-15", "2023-12-01", None])
    assert steps.tolist() == [0, 2, -1, -1, -1]
    weekly = Calendar(Frequency.WEEKLY, pd.Timestamp("2024-01-01"))
    assert weekly.steps_of(["2024-01-15", "2024-01-16"]).tolist() == [2, -1]


@pytest.mark.parametrize(
    ("frequency", "anchor", "month_end"),
    [
        (Frequency.DAILY, "2024-02-27", False),
        (Frequency.WEEKLY, "2024-02-27", False),
        (Frequency.MONTHLY, "2024-01-31", True),
        (Frequency.MONTHLY, "2024-01-30", False),
        (Frequency.QUARTERLY, "2023-11-30", True),
    ],
)
def test_steps_of_and_dates_at_are_inverse(frequency, anchor, month_end):
    calendar = Calendar(frequency, pd.Timestamp(anchor), month_end)
    steps = np.arange(0, 50)
    assert calendar.steps_of(calendar.dates_at(steps)).tolist() == steps.tolist()


def test_horizon_must_be_at_least_one():
    with pytest.raises(ValueError, match="horizon"):
        Calendar(Frequency.DAILY, pd.Timestamp("2024-01-01")).future_dates(10, 0)
