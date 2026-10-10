"""Tool: get_lake_trend

Pool elevation, guide curve deviation and Percent Util for one LRL lake over the
last 1-14 days: start vs. latest, 24-hour change, rising/falling/steady, peak and
low, and the elevation readings themselves. Answers "Is Patoka rising?", "How
much did Cave Run come up after the rain?" and "Is Carr Creek getting closer to
its guide curve?".

Sources: CWMS elevation (lrldlb-rev, ~6-hourly) and storage (lrldlb-comp,
hourly) time series, plus the guide curve and storage levels at the start and
end of the window.
"""

from __future__ import annotations

import asyncio
import datetime
from typing import Annotated, Any

from fastmcp import FastMCP
from pydantic import Field

from lrl_reservoirs.lakes import LAKES, LakeName
from lrl_reservoirs.models import LakeTrend, output_schema
from lrl_reservoirs.tools.lake_conditions import (
    OFFICE,
    _fetch_guide_curve,
    _fetch_storage_level,
)
from lrl_reservoirs.utils import LAKE_REPORT_URL, UpstreamServiceError, cwms_get

# A change smaller than this over 24 hours is reported as "steady"; the LRL
# Daily Lake Report shows 24-hour change to 0.1 ft.
STEADY_THRESHOLD_FT = 0.1
MAX_DAYS = 14
# Extra hours before the window so the first ~6-hourly reading is included.
WINDOW_PAD_HOURS = 6

Point = tuple[datetime.datetime, float]


def _to_points(ts_data: dict[str, Any]) -> list[Point]:
    """CWMS values rows [ts_ms, value, quality] → sorted (utc, value), no nulls."""
    points: list[Point] = []
    for row in ts_data.get("values") or []:
        if len(row) < 2 or row[1] is None:
            continue
        t = datetime.datetime.fromtimestamp(int(row[0]) / 1000, datetime.timezone.utc)
        points.append((t, float(row[1])))
    points.sort(key=lambda p: p[0])
    return points


async def _fetch_series(
    ts_id: str, unit: str, begin: datetime.datetime, end: datetime.datetime
) -> list[Point]:
    try:
        data = await cwms_get(
            "timeseries",
            params={
                "name": ts_id,
                "office": OFFICE,
                "unit": unit,
                "begin": begin.strftime("%Y-%m-%dT%H:%M:%S"),
                "end": end.strftime("%Y-%m-%dT%H:%M:%S"),
                "page-size": "2000",
            },
        )
    except UpstreamServiceError:
        return []
    return _to_points(data)


def _nearest(points: list[Point], at: datetime.datetime) -> Point | None:
    if not points:
        return None
    return min(points, key=lambda p: abs((p[0] - at).total_seconds()))


def _iso(t: datetime.datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _percent_util(
    storage: float | None, at_guide: float | None, at_flood: float | None
) -> float | None:
    if storage is None or at_guide is None or at_flood is None:
        return None
    if at_flood == at_guide:
        return None
    return round((storage - at_guide) / (at_flood - at_guide) * 100, 1)


def _direction(change_ft: float | None) -> str:
    if change_ft is None:
        return "insufficient_data"
    if change_ft > STEADY_THRESHOLD_FT:
        return "rising"
    if change_ft < -STEADY_THRESHOLD_FT:
        return "falling"
    return "steady"


def _guide_trend(dev_start: float | None, dev_end: float | None) -> str:
    """Whether the pool moved toward or away from its guide curve."""
    if dev_start is None or dev_end is None:
        return "unknown"
    delta = abs(dev_end) - abs(dev_start)
    if abs(delta) <= STEADY_THRESHOLD_FT:
        return "no_change"
    return "moving_away_from_guide" if delta > 0 else "moving_toward_guide"


def _round(value: float | None, ndigits: int) -> float | None:
    return None if value is None else round(value, ndigits)


async def _snapshot(
    lake_id: str, elev: Point, storage_points: list[Point]
) -> dict[str, Any]:
    """Elevation, guide curve, deviation and Percent Util at one reading."""
    t, elevation = elev
    guide, at_guide, at_flood = await asyncio.gather(
        _fetch_guide_curve(lake_id, t),
        _fetch_storage_level(lake_id, "Bottom of Flood Control", t),
        _fetch_storage_level(lake_id, "Top of Flood", t),
    )
    stor = _nearest(storage_points, t)
    storage = stor[1] if stor is not None else None
    deviation = round(elevation - guide, 2) if guide is not None else None
    return {
        "time": _iso(t),
        "elevation_ft": round(elevation, 2),
        "guide_curve_ft": guide,
        "deviation_from_guide_curve_ft": deviation,
        "storage_acre_ft": _round(storage, 0),
        "percent_util": _percent_util(storage, at_guide, at_flood),
    }


def _diff(a: float | None, b: float | None, ndigits: int) -> float | None:
    if a is None or b is None:
        return None
    return round(b - a, ndigits)


async def get_lake_trend(
    lake: Annotated[
        LakeName,  # type: ignore[valid-type]
        "CWMS location ID of the LRL lake (e.g. 'Patoka', 'CaveRun').",
    ],
    days: Annotated[
        int,
        Field(ge=1, le=MAX_DAYS, description="Days to look back (1-14). Default 3."),
    ] = 3,
) -> dict[str, Any]:
    """Show how one Louisville District lake has changed over the last few days.

    Use this tool for questions about change over time: "Is Patoka rising or
    falling?", "How much has Cave Run come up since the rain?", "Is Carr Creek
    getting closer to its guide curve?", "What was the peak at Brookville this
    week?". For a single current snapshot use get_lake_conditions; to compare
    with the official morning report use check_against_daily_report.

    Elevation readings come about every 6 hours and storage every hour, so a
    1-day window has only about 4 elevation readings.

    Returns a dict with:
      - lake_id, public_name, basin, days. Elevations are feet, NGVD-29.
      - start / latest (dict): time (UTC), elevation_ft, guide_curve_ft,
          deviation_from_guide_curve_ft, storage_acre_ft, percent_util — for
          the first and most recent elevation readings in the window.
      - change (dict): elevation_ft, deviation_from_guide_curve_ft,
          percent_util and storage_acre_ft (latest minus start).
      - change_24hr_ft (float | null): latest elevation minus the reading
          closest to 24 hours earlier (the report's "24 Hour Change").
      - direction_24hr (str): rising | falling | steady | insufficient_data
          (steady means within 0.1 ft over 24 hours).
      - direction_over_window (str): same categories, latest vs start.
      - guide_curve_trend (str): moving_toward_guide | moving_away_from_guide |
          no_change | unknown — whether the pool got closer to or farther from
          its guide curve over the window.
      - peak / low (dict): time and elevation_ft of the highest and lowest
          readings in the window.
      - readings (list): every elevation reading in the window
          (time, elevation_ft), oldest first.
      - data_note (str); error (str) only when no elevation data was found.

    Report changes factually with times. A lake above its guide curve after
    rain is storing flood water as designed; do not make flood risk judgments.
    The official source is the LRL Daily Lake Report.
    """
    lake_id: str = lake.value  # type: ignore[union-attr]
    meta = LAKES[lake_id]
    days = max(1, min(int(days), MAX_DAYS))

    now = datetime.datetime.now(datetime.timezone.utc)
    window_start = now - datetime.timedelta(days=days)
    begin = window_start - datetime.timedelta(hours=WINDOW_PAD_HOURS)

    elev_points, stor_points = await asyncio.gather(
        _fetch_series(meta["elev_ts_id"], "ft", begin, now),
        _fetch_series(meta["stor_ts_id"], "ac-ft", begin, now),
    )
    base: dict[str, Any] = {
        "lake_id": lake_id,
        "public_name": meta["public_name"],
        "basin": meta["basin"],
        "days": days,
    }
    if len(elev_points) < 2:
        return {
            **base,
            "error": "Not enough elevation readings in the window to show a trend.",
            "observation_count": len(elev_points),
            "data_note": f"See the LRL Daily Lake Report: {LAKE_REPORT_URL}",
        }

    # First reading at or after the window start (the pad only guarantees one).
    in_window = [p for p in elev_points if p[0] >= window_start] or elev_points
    if len(in_window) < 2:
        in_window = elev_points[-2:]
    first, last = in_window[0], in_window[-1]

    start, latest = await asyncio.gather(
        _snapshot(lake_id, first, stor_points),
        _snapshot(lake_id, last, stor_points),
    )

    day_before = _nearest(elev_points, last[0] - datetime.timedelta(hours=24))
    change_24hr: float | None = None
    if day_before is not None and day_before != last:
        hours_apart = (last[0] - day_before[0]).total_seconds() / 3600
        if 18 <= hours_apart <= 30:
            change_24hr = round(last[1] - day_before[1], 2)

    peak = max(in_window, key=lambda p: p[1])
    low = min(in_window, key=lambda p: p[1])
    window_change = round(last[1] - first[1], 2)
    age_hours = round((now - last[0]).total_seconds() / 3600, 1)

    return {
        **base,
        "start": start,
        "latest": latest,
        "change": {
            "elevation_ft": window_change,
            "deviation_from_guide_curve_ft": _diff(
                start["deviation_from_guide_curve_ft"],
                latest["deviation_from_guide_curve_ft"],
                2,
            ),
            "percent_util": _diff(start["percent_util"], latest["percent_util"], 1),
            "storage_acre_ft": _diff(
                start["storage_acre_ft"], latest["storage_acre_ft"], 0
            ),
        },
        "change_24hr_ft": change_24hr,
        "direction_24hr": _direction(change_24hr),
        "direction_over_window": _direction(window_change),
        "guide_curve_trend": _guide_trend(
            start["deviation_from_guide_curve_ft"],
            latest["deviation_from_guide_curve_ft"],
        ),
        "peak": {"time": _iso(peak[0]), "elevation_ft": round(peak[1], 2)},
        "low": {"time": _iso(low[0]), "elevation_ft": round(low[1], 2)},
        "observation_count": len(in_window),
        "readings": [
            {"time": _iso(t), "elevation_ft": round(v, 2)} for t, v in in_window
        ],
        "data_note": (
            f"Latest elevation reading is {age_hours} hours old ({_iso(last[0])}). "
            "Always give reading times. Official source: LRL Daily Lake Report "
            f"{LAKE_REPORT_URL}"
        ),
    }


def register(mcp: FastMCP) -> None:
    """Register the get_lake_trend tool with the MCP server."""
    mcp.tool(
        name="get_lake_trend",
        output_schema=output_schema(LakeTrend),
        annotations={
            "title": "Get LRL Lake Trend",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )(get_lake_trend)
