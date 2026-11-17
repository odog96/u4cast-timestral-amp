"""Frequencies, the regular date grid of a dataset, and future dates.

Timestral takes numbers only. Everything about dates lives here: which
frequency a set of dates has, which step of the grid a date is, and which
dates follow the end of a history.
"""

from __future__ import annotations

import enum
from collections.abc import Iterable
from dataclasses import dataclass

import numpy as np
import pandas as pd


class Frequency(enum.StrEnum):
    """The frequencies version one supports. Nothing finer than daily."""

    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"


# Steps in one seasonal cycle. Used by the backtest's reference comparison,
# which repeats the value from one season earlier.
SEASON_LENGTH: dict[Frequency, int] = {
    Frequency.DAILY: 7,
    Frequency.WEEKLY: 52,
    Frequency.MONTHLY: 12,
    Frequency.QUARTERLY: 4,
}

# Decision 28 in docs/decisions.md: two years for monthly, quarterly and
# weekly data, 32 steps for daily data.
DEFAULT_MINIMUM_HISTORY: dict[Frequency, int] = {
    Frequency.DAILY: 32,
    Frequency.WEEKLY: 104,
    Frequency.MONTHLY: 24,
    Frequency.QUARTERLY: 8,
}

# The lowest minimum a user's override can set, where the default is higher.
OVERRIDE_FLOOR = 32

# Gaps in days between consecutive dates that mark each frequency.
_GAP_DAYS: dict[Frequency, tuple[int, int]] = {
    Frequency.DAILY: (1, 1),
    Frequency.WEEKLY: (7, 7),
    Frequency.MONTHLY: (28, 31),
    Frequency.QUARTERLY: (89, 92),
}

_MONTHS_PER_STEP: dict[Frequency, int] = {Frequency.MONTHLY: 1, Frequency.QUARTERLY: 3}


def minimum_history(frequency: Frequency, override: int | None = None) -> int:
    """Return the minimum number of history steps a series must have.

    Without an override this is the default for the frequency. An override
    replaces the default, but cannot go below 32 steps, or below the default
    where the default is already under 32.
    """
    default = DEFAULT_MINIMUM_HISTORY[frequency]
    if override is None:
        return default
    lowest = min(default, OVERRIDE_FLOOR)
    if override < lowest:
        raise ValueError(
            f"The minimum history for {frequency.value} data cannot be set below {lowest} steps; "
            f"got {override}."
        )
    return override


@dataclass(frozen=True)
class FrequencyGuess:
    """What `detect_frequency` found. The user confirms or corrects it.

    `share` is the share of gaps between consecutive dates that fit the
    guessed frequency. It is 0.0 when no frequency could be guessed.
    """

    frequency: Frequency | None
    share: float


def _as_days(dates: Iterable[object]) -> pd.DatetimeIndex:
    """Turn any collection of dates into days, keeping the order and any missing date."""
    if not isinstance(dates, pd.Series | pd.Index | np.ndarray):
        dates = list(dates)
    return pd.DatetimeIndex(pd.to_datetime(np.asarray(dates))).normalize()


def _unique_days(dates: Iterable[object]) -> pd.DatetimeIndex:
    days = _as_days(dates)
    return days[~days.isna()].unique().sort_values()


def detect_frequency(dates: Iterable[object]) -> FrequencyGuess:
    """Guess the frequency of a dataset from its dates.

    The guess is the frequency whose gap between consecutive distinct dates
    is the most common. Missing dates do not confuse it as long as most
    consecutive dates are one step apart.
    """
    days = _unique_days(dates)
    if len(days) < 3:
        return FrequencyGuess(None, 0.0)
    gaps = np.diff(days.values).astype("timedelta64[D]").astype(int)
    counts = {
        frequency: int(((gaps >= low) & (gaps <= high)).sum())
        for frequency, (low, high) in _GAP_DAYS.items()
    }
    best = max(counts, key=lambda frequency: counts[frequency])
    if counts[best] == 0:
        return FrequencyGuess(None, 0.0)
    return FrequencyGuess(best, counts[best] / len(gaps))


def _most_common(values: np.ndarray) -> int:
    uniques, counts = np.unique(values, return_counts=True)
    return int(uniques[np.argmax(counts)])


@dataclass(frozen=True)
class Calendar:
    """The regular date grid of a dataset: step 0 is `anchor`, step 1 the next period.

    For monthly and quarterly data `month_end` says whether dates are the last
    day of the month. Otherwise they keep the anchor's day of the month, or
    the last day of a month that is shorter than that.
    """

    frequency: Frequency
    anchor: pd.Timestamp
    month_end: bool = False

    @classmethod
    def from_dates(cls, frequency: Frequency, dates: Iterable[object]) -> Calendar:
        """Build the grid that fits most of the given dates.

        The grid starts at the earliest date that follows the dataset's own
        pattern: its usual weekday for weekly data, its usual day of the month
        for monthly and quarterly data.
        """
        days = _unique_days(dates)
        if len(days) == 0:
            raise ValueError("Cannot build a date grid from no dates.")
        if frequency is Frequency.DAILY:
            return cls(frequency, days[0])
        if frequency is Frequency.WEEKLY:
            weekday = _most_common(days.day_of_week.to_numpy())
            return cls(frequency, days[days.day_of_week == weekday][0])
        month_end = bool(days.is_month_end.mean() > 0.5)
        if month_end:
            candidates = days[days.is_month_end]
        else:
            candidates = days[days.day == _most_common(days.day.to_numpy())]
        if frequency is Frequency.QUARTERLY:
            phase = _most_common(candidates.month.to_numpy() % 3)
            candidates = candidates[candidates.month % 3 == phase]
        return cls(frequency, candidates[0], month_end)

    def dates_at(self, steps: np.ndarray) -> pd.DatetimeIndex:
        """Return the date of each grid step. Steps can be negative."""
        steps = np.asarray(steps, dtype=np.int64)
        if self.frequency is Frequency.DAILY:
            return pd.DatetimeIndex(self.anchor + pd.to_timedelta(steps, unit="D"))
        if self.frequency is Frequency.WEEKLY:
            return pd.DatetimeIndex(self.anchor + pd.to_timedelta(steps * 7, unit="D"))
        months = steps * _MONTHS_PER_STEP[self.frequency]
        periods = pd.PeriodIndex.from_ordinals(
            self.anchor.to_period("M").ordinal + months, freq="M"
        )
        last_day = periods.days_in_month.to_numpy()
        day = last_day if self.month_end else np.minimum(self.anchor.day, last_day)
        return pd.DatetimeIndex(
            periods.to_timestamp(how="start") + pd.to_timedelta(day - 1, unit="D")
        )

    def dates(self, first_step: int, count: int) -> pd.DatetimeIndex:
        """Return `count` consecutive grid dates starting at `first_step`."""
        return self.dates_at(np.arange(first_step, first_step + count))

    def future_dates(self, history_length: int, horizon: int) -> pd.DatetimeIndex:
        """Return the `horizon` dates that follow a history of `history_length` steps."""
        if horizon < 1:
            raise ValueError(f"The horizon must be at least 1; got {horizon}.")
        return self.dates(history_length, horizon)

    def steps_of(self, dates: Iterable[object]) -> np.ndarray:
        """Return the grid step of each date, or -1 for a date that is not on the grid.

        A date before the anchor is not on the grid.
        """
        days = _as_days(dates)
        if len(days) == 0:
            return np.empty(0, dtype=np.int64)
        known = ~days.isna()
        days = days.where(known, self.anchor)
        if self.frequency in (Frequency.DAILY, Frequency.WEEKLY):
            width = 1 if self.frequency is Frequency.DAILY else 7
            offset = (days - self.anchor).days.to_numpy()
        else:
            width = _MONTHS_PER_STEP[self.frequency]
            offset = (
                (days.year - self.anchor.year) * 12 + (days.month - self.anchor.month)
            ).to_numpy()
        steps = np.asarray(offset, dtype=np.int64) // width
        on_grid = known & (steps >= 0) & np.asarray(self.dates_at(steps) == days)
        return np.where(on_grid, steps, -1)
