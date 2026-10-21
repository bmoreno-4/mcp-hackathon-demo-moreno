"""Ready-made Eastern time labels for tool output.

The Louisville District reports in Eastern time, and the public reads dates and
times that way. Models are unreliable at converting UTC and at working out the
day of the week (a 2026-10-21 briefing called Wednesday "Friday"), so the tools
return these labels and the agent is told to use them exactly as given.
"""

from __future__ import annotations

import datetime
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")


def eastern_date_label(value: datetime.date | datetime.datetime) -> str:
    """'Wednesday, October 21, 2026'. Aware datetimes are converted first."""
    if isinstance(value, datetime.datetime):
        value = value.astimezone(EASTERN).date()
    return f"{value:%A, %B} {value.day}, {value.year}"


def eastern_time_label(value: datetime.datetime) -> str:
    """'6:00 AM EDT' (or EST in winter) for an aware datetime."""
    local = value.astimezone(EASTERN)
    hour = local.hour % 12 or 12
    return f"{hour}:{local:%M %p} {local.tzname()}"


def eastern_datetime_label(value: datetime.datetime | None) -> str | None:
    """'Wednesday, October 21, 2026, 6:00 AM EDT', or None."""
    if value is None:
        return None
    return f"{eastern_date_label(value)}, {eastern_time_label(value)}"
