"""Tool: check_against_daily_report

Fetches today's official USACE LRL Daily Lake Report and compares it lake by
lake with the live CWMS values this server computes (pool elevation, guide
curve and Percent Util). It answers "does the server agree with the official
report?" and "what has changed since the 6 AM report?" in one call.

Two outbound sources: the LRL report page (fixed URL) and the CWMS Data API
(via get_lake_conditions).
"""

from __future__ import annotations

import asyncio
import datetime
from typing import Annotated, Any

from fastmcp import FastMCP

from lrl_reservoirs.lake_report import (
    REPORT_TIMEZONE,
    parse_report_date,
    parse_report_rows,
    report_reference_time,
)
from lrl_reservoirs.lakes import LAKES, LakeName
from lrl_reservoirs.models import ReportCheck, output_schema
from lrl_reservoirs.tools.lake_conditions import get_lake_conditions
from lrl_reservoirs.utils import (
    LAKE_REPORT_URL,
    UpstreamServiceError,
    fetch_lake_report_html,
)

# The report rounds elevations and deviations to 0.1 ft and Percent Util to
# 0.01; these tolerances match eval/lrl_percent_util_validation.py plus rounding.
POOL_TOL_FT = 0.15
GUIDE_TOL_FT = 0.15
PERCENT_UTIL_TOL = 0.15


def _parse_iso_utc(value: str | None) -> datetime.datetime | None:
    if not value:
        return None
    try:
        return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _diff(live: float | None, report: float | None) -> float | None:
    if live is None or report is None:
        return None
    return round(live - report, 2)


def compare_lake(
    lake_id: str,
    report_row: dict[str, Any] | None,
    live: dict[str, Any],
    reference_time: datetime.datetime,
) -> dict[str, Any]:
    """Compare one lake's live CWMS result with its Daily Lake Report row."""
    meta = LAKES[lake_id]
    entry: dict[str, Any] = {
        "lake_id": lake_id,
        "public_name": meta["public_name"],
        "basin": meta["basin"],
        "live_as_of": live.get("as_of"),
    }
    if report_row is None:
        entry["status"] = "not_in_report"
        return entry

    report_pool = report_row.get("todays_pool_ft")
    report_dev = report_row.get("dev_from_pool_ft")
    report_guide = (
        round(report_pool - report_dev, 2)
        if report_pool is not None and report_dev is not None
        else None
    )
    entry["report"] = {
        "pool_ft": report_pool,
        "dev_from_guide_ft": report_dev,
        "guide_curve_ft": report_guide,
        "percent_util": report_row.get("percent_util"),
        "change_24hr_ft": report_row.get("change_24hr_ft"),
        "precip_24hr_in": report_row.get("precip_24hr_in"),
        "inflow_24hr_avg_cfs": report_row.get("inflow_24hr_cfs"),
        "outflow_6am_cfs": report_row.get("outflow_6am_cfs"),
    }

    if live.get("elevation_ft") is None:
        entry["status"] = "no_live_data"
        entry["reason"] = live.get("error") or "no recent CWMS elevation"
        return entry

    entry["live"] = {
        "pool_ft": live.get("elevation_ft"),
        "dev_from_guide_ft": live.get("deviation_from_guide_curve_ft"),
        "guide_curve_ft": live.get("guide_curve_ft"),
        "percent_util": live.get("percent_util"),
        "pool_status": live.get("pool_status"),
    }
    differences = {
        "pool_ft": _diff(live.get("elevation_ft"), report_pool),
        "guide_curve_ft": _diff(live.get("guide_curve_ft"), report_guide),
        "percent_util": _diff(live.get("percent_util"), report_row.get("percent_util")),
    }
    entry["live_minus_report"] = differences

    tolerances = {
        "pool_ft": POOL_TOL_FT,
        "guide_curve_ft": GUIDE_TOL_FT,
        "percent_util": PERCENT_UTIL_TOL,
    }
    outside = [
        field
        for field, d in differences.items()
        if d is not None and abs(d) > tolerances[field]
    ]
    entry["outside_tolerance"] = outside

    obs = _parse_iso_utc(live.get("as_of"))
    live_is_newer = obs is not None and obs > reference_time
    if not outside:
        entry["status"] = "matches"
    elif live_is_newer:
        entry["status"] = "changed_since_report"
    else:
        entry["status"] = "differs"
    return entry


async def check_against_daily_report(
    lake: Annotated[
        LakeName | None,  # type: ignore[valid-type]
        "Optional CWMS lake ID (e.g. 'Patoka'). Omit to check all 17 lakes.",
    ] = None,
) -> dict[str, Any]:
    """Compare live CWMS lake conditions with today's official LRL Daily Lake
    Report, lake by lake.

    Use this tool for questions such as "Does the server match the official lake
    report?", "What has changed since this morning's report?", "Which lakes got
    rain in the last 24 hours?" or "Is Patoka higher now than in the 6 AM
    report?". It also returns the report's 24-hour precipitation, 24-hour
    change, average inflow and 6 AM outflow for each lake.

    The report is published once a day for 06:00 Eastern. Live CWMS elevation
    updates about every 6 hours and storage every hour, so after 06:00 a
    difference usually means the lake has changed (for example after rain), not
    that either source is wrong.

    Per-lake status:
      - matches: pool, guide curve and Percent Util all within tolerance.
      - changed_since_report: outside tolerance and the live observation is
          newer than the report time — describe it as a change since 6 AM.
      - differs: outside tolerance although the live observation is not newer
          than the report — a real discrepancy; point users to the report.
      - no_live_data: CWMS had no recent elevation for this lake.
      - not_in_report: the lake was missing from today's report.

    Returns a dict with:
      - report_date (str), report_reference_time_utc (str), report_is_today
          (bool; false before the morning report is published), report_url.
      - tolerances (dict): pool_ft, guide_curve_ft, percent_util.
      - counts (dict): number of lakes per status.
      - lakes (list): lake_id, public_name, basin, status, live_as_of,
          report {pool_ft, dev_from_guide_ft, guide_curve_ft, percent_util,
          change_24hr_ft, precip_24hr_in, inflow_24hr_avg_cfs, outflow_6am_cfs},
          live {pool_ft, dev_from_guide_ft, guide_curve_ft, percent_util,
          pool_status}, live_minus_report {pool_ft, guide_curve_ft,
          percent_util}, outside_tolerance (list of field names).
      - error (str): present only when the report could not be fetched or read.

    Report the values factually with both timestamps. Do not make flood risk
    judgments; the official report is the authoritative source.
    """
    try:
        html = await fetch_lake_report_html()
        report_date = parse_report_date(html)
        rows = parse_report_rows(html)
    except UpstreamServiceError as exc:
        return {"error": str(exc), "report_url": LAKE_REPORT_URL}
    except ValueError:
        return {
            "error": "The lake report page could not be read; its format may "
            "have changed.",
            "report_url": LAKE_REPORT_URL,
        }

    reference_time = report_reference_time(report_date)
    today_et = datetime.datetime.now(REPORT_TIMEZONE).date()

    if lake is not None:
        lake_names = [lake]
    else:
        lake_names = [LakeName(lid) for lid in LAKES]  # type: ignore[call-arg]
    lake_ids = [ln.value for ln in lake_names]  # type: ignore[union-attr]
    live_results = await asyncio.gather(
        *[get_lake_conditions(lake=ln) for ln in lake_names]
    )
    entries = [
        compare_lake(lid, rows.get(lid), live, reference_time)
        for lid, live in zip(lake_ids, live_results)
    ]
    entries.sort(key=lambda e: (e["basin"], e["public_name"]))

    counts: dict[str, int] = {
        "matches": 0,
        "changed_since_report": 0,
        "differs": 0,
        "no_live_data": 0,
        "not_in_report": 0,
    }
    for e in entries:
        counts[e["status"]] += 1

    return {
        "report_date": report_date.isoformat(),
        "report_reference_time_utc": reference_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "report_is_today": report_date == today_et,
        "report_url": LAKE_REPORT_URL,
        "tolerances": {
            "pool_ft": POOL_TOL_FT,
            "guide_curve_ft": GUIDE_TOL_FT,
            "percent_util": PERCENT_UTIL_TOL,
        },
        "counts": counts,
        "lakes": entries,
    }


def register(mcp: FastMCP) -> None:
    """Register the check_against_daily_report tool with the MCP server."""
    mcp.tool(
        name="check_against_daily_report",
        output_schema=output_schema(ReportCheck),
        annotations={
            "title": "Check Against LRL Daily Lake Report",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )(check_against_daily_report)
