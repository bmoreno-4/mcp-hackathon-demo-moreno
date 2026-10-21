"""Tests for the Eastern date and time labels returned by the tools."""

from __future__ import annotations

import datetime

from lrl_reservoirs.eastern_time import (
    eastern_date_label,
    eastern_datetime_label,
    eastern_time_label,
)

UTC = datetime.timezone.utc


def test_weekday_is_correct():
    """2026-10-21 briefing called Wednesday 'Friday'."""
    assert eastern_date_label(datetime.date(2026, 10, 21)) == (
        "Wednesday, October 21, 2026"
    )


def test_report_time_in_daylight_time():
    six_am = datetime.datetime(2026, 10, 21, 10, 0, tzinfo=UTC)
    assert eastern_time_label(six_am) == "6:00 AM EDT"


def test_standard_time_after_daylight_saving_ends():
    # Daylight saving time ends 2026-11-01; 06:00 EST is 11:00 UTC.
    six_am = datetime.datetime(2026, 11, 2, 11, 0, tzinfo=UTC)
    assert eastern_time_label(six_am) == "6:00 AM EST"
    assert eastern_date_label(six_am) == "Monday, November 2, 2026"


def test_utc_evening_is_still_the_same_eastern_day():
    # 02:30 UTC on Oct 22 is 10:30 PM on Wednesday Oct 21 in Eastern time.
    late = datetime.datetime(2026, 10, 22, 2, 30, tzinfo=UTC)
    assert eastern_datetime_label(late) == ("Wednesday, October 21, 2026, 10:30 PM EDT")


def test_noon_and_midnight():
    noon = datetime.datetime(2026, 10, 21, 16, 0, tzinfo=UTC)
    midnight = datetime.datetime(2026, 10, 21, 4, 5, tzinfo=UTC)
    assert eastern_time_label(noon) == "12:00 PM EDT"
    assert eastern_time_label(midnight) == "12:05 AM EDT"


def test_none_passes_through():
    assert eastern_datetime_label(None) is None
