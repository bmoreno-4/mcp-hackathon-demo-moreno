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

Interpretation guidance for AI assistants
------------------------------------------
- All status values (above_guide, below_guide, etc.) are relative to the live
  guide curve (Bottom of Flood Control) for the current date — NOT the static
  summer_pool or winter_pool reference values.
- Do NOT characterise lakes as "below summer pool" or "below conservation pool"
  as shortfalls; deviations from those static targets are expected and normal
  during seasonal fill and drawdown periods.
- Always include the observation timestamp (as_of per lake, as_of_utc for the
  snapshot) when reporting conditions to users.
- Report conditions factually (counts, elevation, deviation, pool_status).
  Do not make operational judgments or flood risk assessments such as "no
  concern" or "normal conditions".
- For official lake conditions, direct users to the LRL Daily Lake Report:
  https://www.lrl-wc.usace.army.mil/reports/lkreport.html
"""

from __future__ import annotations

import asyncio
from enum import Enum
from typing import Annotated, Any

from fastmcp import FastMCP

from lrl_reservoirs.lakes import LAKES, LakeName
from lrl_reservoirs.tools.lake_conditions import get_lake_conditions

# Status values that count as "no real data"
_NO_DATA_STATUSES = {"no_data"}

# ── Enums for optional filters ────────────────────────────────────────────────

# Build basin enum dynamically from the lake table.
_BASINS = sorted({meta["basin"] for meta in LAKES.values()})
BasinName = Enum(  # type: ignore[misc]
    "BasinName",
    {b.replace(" ", "_").replace(".", ""): b for b in _BASINS},
    type=str,
)
BasinName.__doc__ = (
    "River basin served by one or more LRL reservoirs. "
    "Use to filter summarize_district_lakes to a single basin. "
    f"Values: {', '.join(_BASINS)}."
)

PoolStatusFilter = Enum(  # type: ignore[misc]
    "PoolStatusFilter",
    {
        "above_guide": "above_guide",
        "at_guide": "at_guide",
        "below_guide": "below_guide",
        "at_or_above_flood": "at_or_above_flood",
        "no_data": "no_data",
    },
    type=str,
)
PoolStatusFilter.__doc__ = (
    "Pool status bucket for filtering summarize_district_lakes results. "
    "Matches the aggregate count keys: above_guide | at_guide | below_guide | "
    "at_or_above_flood | no_data."
)


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


async def summarize_district_lakes(
    basin: Annotated[
        BasinName | None,  # type: ignore[valid-type]
        "Optional: restrict results to a single river basin "
        "(e.g. 'Green River', 'Salt River').",
    ] = None,
    status: Annotated[
        PoolStatusFilter | None,  # type: ignore[valid-type]
        "Optional: restrict results to lakes with a specific pool status bucket "
        "(above_guide | at_guide | below_guide | at_or_above_flood | no_data).",
    ] = None,
) -> dict[str, Any]:
    """Return current pool conditions for all 17 USACE Louisville District lakes.

    Queries every reservoir in parallel (same data sources as get_lake_conditions)
    and returns a district-wide snapshot sorted by basin then public name.

    Optional filters narrow the returned lake list without changing the aggregate
    counts, which always reflect the full district.

    Returns a dict with:
      - as_of_utc (str): ISO-8601 UTC timestamp of the tool call —
          ALWAYS include this when reporting district conditions to users.
      - total_lakes (int): Number of lakes in the district (always 17)
      - counts (dict): Aggregate status breakdown relative to today's guide curve —
          above_guide, at_guide, below_guide, at_or_above_flood, no_data.
          All status buckets are relative to the live guide curve (Bottom of Flood
          Control), not to static summer/winter pool reference values.
      - data_note (str): One-line guidance reminding the caller that all status
          values use the live guide curve; summer/winter pool are reference-only;
          always cite as_of_utc; report factually without operational judgments;
          and direct users to the LRL Daily Lake Report for official information.
      - lakes (list[dict]): One entry per lake (filtered when basin/status is set),
          each identical in shape to the dict returned by get_lake_conditions
          (including a per-lake data_note). Sorted by basin then public_name.
          Lakes with fetch errors include an "error" key; all other fields are null.

    Interpretation guidance for AI assistants:
      - All status values (above_guide, below_guide, etc.) are relative to the live
        guide curve (Bottom of Flood Control) for the current date — NOT the static
        summer_pool or winter_pool reference values.
      - Do not characterise lakes as "below summer pool" or "below conservation pool"
        as shortfalls; deviations from those static targets are expected and normal
        during seasonal fill and drawdown periods.
      - Always include the observation timestamp (as_of per lake, as_of_utc for the
        snapshot) when reporting conditions to users.
      - Report conditions factually (counts, elevation, deviation, pool_status).
        Do not make operational judgments or flood risk assessments such as "no
        concern" or "normal conditions".
      - For official lake conditions, direct users to the LRL Daily Lake Report:
        https://www.lrl-wc.usace.army.mil/reports/lkreport.html
    """
    import datetime

    now_utc = datetime.datetime.now(datetime.timezone.utc)

    # Limit concurrency to avoid saturating the CWMS API.
    # Each per-lake call itself issues up to 5 sub-requests in parallel, so
    # 8 concurrent lake calls ≈ up to 40 concurrent HTTP connections at peak.
    _sem = asyncio.Semaphore(8)

    async def _fetch(ln) -> dict[str, Any]:
        async with _sem:
            return await get_lake_conditions(lake=ln)

    # Fetch all lakes concurrently — LakeName is a dynamic Enum
    lake_names = [LakeName(lid) for lid in LAKES]  # type: ignore[call-arg]
    results: list[dict[str, Any]] = await asyncio.gather(
        *[_fetch(ln) for ln in lake_names],
        return_exceptions=False,
    )

    # Sort by basin then public_name for a consistent, readable ordering
    results.sort(key=lambda r: (r.get("basin", ""), r.get("public_name", "")))

    # Tally aggregate counts (always over the full district, before filtering)
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

    # Apply optional filters to the returned lake list
    basin_value: str | None = basin.value if basin is not None else None  # type: ignore[union-attr]
    status_value: str | None = status.value if status is not None else None  # type: ignore[union-attr]

    filtered = results
    if basin_value is not None:
        filtered = [r for r in filtered if r.get("basin") == basin_value]
    if status_value is not None:
        filtered = [
            r
            for r in filtered
            if _status_bucket(r.get("pool_status", "no_data")) == status_value
        ]

    return {
        "as_of_utc": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "total_lakes": len(results),
        "counts": counts,
        "data_note": (
            "All status counts use today's live guide curve (Bottom of Flood Control); "
            "summer/winter pool are reference-only; always cite as_of_utc; "
            "report factually without operational judgments; "
            "direct users to https://www.lrl-wc.usace.army.mil/reports/lkreport.html (LRL Daily Lake Report) for official information."
        ),
        "lakes": filtered,
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
