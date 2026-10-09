"""Tool: summarize_district_lakes

Fetches current conditions for every USACE Louisville District (LRL) reservoir
in parallel and returns a district-wide summary sorted by basin then lake name.

Each lake entry is the same shape as get_lake_conditions, so any field that
would be null for a single lake is also null here.  Failed lakes include an
"error" key and are still included in the list so callers can see which lakes
had data problems.

Aggregate counts (at the top level) give a quick district overview:
  - total_lakes          number of lakes in the district
  - above_guide          lakes above today's guide curve
  - at_guide             lakes within ±0.05 ft of guide curve
  - below_guide          lakes below today's guide curve
  - at_or_above_flood    lakes at or above flood pool
  - no_data              lakes with fetch errors
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastmcp import FastMCP

from lrl_reservoirs.tools.lake_conditions import (
    _LAKES,
    LakeName,
    get_lake_conditions,
)

# Status values that count as "no real data"
_NO_DATA_STATUSES = {"no_data"}


def _status_bucket(pool_status: str) -> str:
    """Map a pool_status string to one of the aggregate count keys."""
    if pool_status in _NO_DATA_STATUSES:
        return "no_data"
    if pool_status == "at_or_above_flood":
        return "at_or_above_flood"
    if pool_status == "above_guide":
        return "above_guide"
    if pool_status == "at_guide":
        return "at_guide"
    if pool_status == "below_guide":
        return "below_guide"
    # "no_guide/*" fall-backs count as no_data for district summary purposes
    return "no_data"


async def summarize_district_lakes() -> dict[str, Any]:
    """Return current pool conditions for all 17 USACE Louisville District lakes.

    Queries every reservoir in parallel (same data sources as get_lake_conditions)
    and returns a district-wide snapshot sorted by basin then public name.

    Returns a dict with:
      - as_of_utc (str): ISO-8601 UTC timestamp of the tool call
      - total_lakes (int): Number of lakes in the district (always 17)
      - counts (dict): Aggregate status breakdown —
          above_guide, at_guide, below_guide, at_or_above_flood, no_data
      - lakes (list[dict]): One entry per lake, each identical in shape to the
          dict returned by get_lake_conditions. Sorted by basin then public_name.
          Lakes with fetch errors include an "error" key; all other fields are null.
    """
    import datetime

    now_utc = datetime.datetime.now(datetime.timezone.utc)

    # Fetch all lakes concurrently — LakeName is a dynamic Enum
    lake_names = [LakeName(lid) for lid in _LAKES]  # type: ignore[call-arg]
    results: list[dict[str, Any]] = await asyncio.gather(
        *[get_lake_conditions(lake=ln) for ln in lake_names],
        return_exceptions=False,
    )

    # Sort by basin then public_name for a consistent, readable ordering
    results.sort(key=lambda r: (r.get("basin", ""), r.get("public_name", "")))

    # Tally aggregate counts
    counts: dict[str, int] = {
        "above_guide": 0,
        "at_guide": 0,
        "below_guide": 0,
        "at_or_above_flood": 0,
        "no_data": 0,
    }
    for r in results:
        bucket = _status_bucket(r.get("pool_status", "no_data"))
        counts[bucket] += 1

    return {
        "as_of_utc": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "total_lakes": len(results),
        "counts": counts,
        "lakes": results,
    }


# ── Tool registration ─────────────────────────────────────────────────────────


def register(mcp: FastMCP) -> None:
    """Register the summarize_district_lakes tool with the MCP server."""
    mcp.tool(
        name="summarize_district_lakes",
        annotations={
            "title": "Summarize LRL District Lakes",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )(summarize_district_lakes)
