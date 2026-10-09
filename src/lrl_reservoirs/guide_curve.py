"""Guide-curve interpolation for CWMS seasonal location levels.

The CWMS "Bottom of Flood Control" level is defined as a set of
(offset-months, offset-minutes, value) anchors relative to an
interval-origin that repeats every interval-months (always 12).
interpolate-string="T" means linear interpolation between anchors.
"""

from __future__ import annotations

import calendar
import datetime
from typing import Any


def _add_months(dt: datetime.datetime, months: int) -> datetime.datetime:
    """Add a whole number of months to a datetime, clamping the day."""
    m = dt.month - 1 + months
    year = dt.year + m // 12
    month = m % 12 + 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def _resolve_anchor(
    origin: datetime.datetime,
    cycle_offset_months: int,
    sv_offset_months: int,
    sv_offset_minutes: int,
) -> datetime.datetime:
    """Convert a seasonal-value anchor to an absolute datetime."""
    base = _add_months(origin, cycle_offset_months + sv_offset_months)
    return base + datetime.timedelta(minutes=sv_offset_minutes)


def interpolate_guide_curve(
    seasonal_values: list[dict[str, Any]],
    interval_origin_str: str,
    interval_months: int,
    query: datetime.datetime,
) -> float:
    """Linearly interpolate CWMS seasonal values at *query*.

    Args:
        seasonal_values: list of dicts with keys offset-months, offset-minutes, value.
        interval_origin_str: ISO-8601 UTC string of the cycle origin.
        interval_months: cycle length in months (always 12 for LRL lakes).
        query: tz-aware UTC datetime to evaluate.

    Returns:
        Interpolated elevation in feet, rounded to 2 decimal places.
    """
    origin = datetime.datetime.fromisoformat(interval_origin_str.replace("Z", "+00:00"))
    # Build anchors for three consecutive cycles to ensure the query is bracketed.
    year_diff = query.year - origin.year
    start_cycle = max(0, year_diff - 1)
    anchors: list[tuple[datetime.datetime, float]] = []
    for n in range(start_cycle, start_cycle + 4):
        cycle_off = interval_months * n
        for sv in seasonal_values:
            t = _resolve_anchor(
                origin, cycle_off, sv["offset-months"], sv["offset-minutes"]
            )
            anchors.append((t, sv["value"]))
    anchors.sort()

    for i in range(len(anchors) - 1):
        t0, v0 = anchors[i]
        t1, v1 = anchors[i + 1]
        if t0 <= query <= t1:
            span = (t1 - t0).total_seconds()
            if span == 0:
                return v0
            frac = (query - t0).total_seconds() / span
            return round(v0 + frac * (v1 - v0), 2)

    # Fallback: closest anchor (should not be reached for well-formed data).
    return round(
        min(anchors, key=lambda tv: abs((tv[0] - query).total_seconds()))[1], 2
    )
