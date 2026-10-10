"""Tool: summarize_district_lakes

Fetches current conditions for every USACE Louisville District (LRL) reservoir
in parallel and returns a district-wide summary sorted by basin then lake name.

Each lake entry is the same shape as get_lake_conditions (including
observation_age_hours and stale), so any field that would be null for a single
lake is also null here.  Failed lakes include an "error" key and are still
included in the list so callers can see which lakes had data problems.

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
from lrl_reservoirs.models import DistrictSummary, output_schema
from lrl_reservoirs.tools.lake_conditions import get_lake_conditions
from lrl_reservoirs.utils import LAKE_REPORT_URL

# Status values that count as "no real data"
_NO_DATA_STATUSES = {"no_data"}

# ── Enums for optional filters ────────────────────────────────────────────────

# Build basin enum dynamically from the lake table.
_BASINS = sorted({meta["basin"] for meta in LAKES.values()})
_BASIN_LAKES: dict[str, list[str]] = {
    b: sorted(m["public_name"] for m in LAKES.values() if m["basin"] == b)
    for b in _BASINS
}
_BASIN_GUIDE = "; ".join(f"'{b}' ({', '.join(_BASIN_LAKES[b])})" for b in _BASINS)
BasinName = Enum(  # type: ignore[misc]
    "BasinName",
    {b.replace(" ", "_").replace(".", ""): b for b in _BASINS},
    type=str,
)
BasinName.__doc__ = (
    "River basin served by one or more LRL reservoirs. "
    "Use to filter summarize_district_lakes to a single basin. "
    f"Exact values and their lakes: {_BASIN_GUIDE}."
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
    "above_guide = using some flood storage; at_or_above_flood = flood pool full. "
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
        "Optional: restrict results to a single river basin. Use the exact value "
        f"(lakes in parentheses): {_BASIN_GUIDE}. "
        "Example: 'Kentucky River basin' -> 'Kentucky'.",
    ] = None,
    status: Annotated[
        PoolStatusFilter | None,  # type: ignore[valid-type]
        "Optional: restrict results to one pool status bucket: above_guide "
        "(pool is above the guide curve, i.e. USING some flood storage) | at_guide "
        "| below_guide | at_or_above_flood (pool at or above the TOP of the flood "
        "pool, i.e. flood storage FULL; rare) | no_data. To find which lake is "
        "using the most flood storage, do NOT filter by status; use "
        "flood_storage_ranking (sorted by percent_util).",
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
          (including observation_age_hours, stale, and data_note per lake).
          Sorted by basin then public_name.
          Lakes with fetch errors include an "error" key; all other fields are null.
      - filters (dict): the basin/status filters applied.
      - lakes_in_scope (int): lakes in the requested basin (17 when no basin).
      - matched_lakes (int): lakes returned after the status filter.
      - unevaluated_lakes (list): lakes in scope with no usable data, so they could
          not be checked against a status filter. Never report these as "not
          matching"; report their status as unknown.
      - filter_note (str | null): plain-language warning when unevaluated_lakes
          is non-empty.
      - flood_storage_ranking (list): lakes in the requested basin (status filter
          not applied) sorted by percent_util, highest first: lake_id,
          public_name, percent_util, deviation_from_guide_curve_ft, as_of.
          Use this for "which lake is using the most flood storage?".
      - lakes_by_status (dict): public names of the lakes in scope for each
          status bucket (above_guide, at_guide, below_guide, at_or_above_flood,
          no_data). Use these lists as-is when naming lakes by status; each lake
          appears exactly once. A lake at_guide can still show a small positive
          percent_util; list it as at guide.

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
      - Terminology: a lake "using flood storage" is any lake above its guide
        curve; how much is percent_util (share of flood storage in use).
        at_or_above_flood means the flood pool is FULL, not "using flood storage".
      - For official lake conditions, direct users to the LRL Daily Lake Report:
        https://www.lrl-wc.usace.army.mil/reports/lkreport.html
    """
    import datetime

    now_utc = datetime.datetime.now(datetime.timezone.utc)

    # Fetch all lakes concurrently — concurrency is capped by the module-level
    # asyncio.Semaphore(8) inside cwms_get, so no per-call wrapper is needed.
    lake_names = [LakeName(lid) for lid in LAKES]  # type: ignore[call-arg]
    results: list[dict[str, Any]] = await asyncio.gather(
        *[get_lake_conditions(lake=ln) for ln in lake_names],
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

    in_scope = results
    if basin_value is not None:
        in_scope = [r for r in in_scope if r.get("basin") == basin_value]
    filtered = in_scope
    unevaluated: list[dict[str, Any]] = []
    if status_value is not None:
        filtered = [
            r
            for r in in_scope
            if _status_bucket(r.get("pool_status", "no_data")) == status_value
        ]
        # Lakes that could not be evaluated must never be silently dropped:
        # "no data" is not the same as "does not match the status filter".
        if status_value != "no_data":
            unevaluated = [
                {
                    "lake_id": r.get("lake_id"),
                    "public_name": r.get("public_name"),
                    "pool_status": r.get("pool_status"),
                    "reason": r.get("error") or "no usable elevation/guide curve data",
                }
                for r in in_scope
                if _status_bucket(r.get("pool_status", "no_data")) == "no_data"
            ]

    ranked = [r for r in in_scope if r.get("percent_util") is not None]
    ranked.sort(key=lambda r: r["percent_util"], reverse=True)
    flood_storage_ranking = [
        {
            "lake_id": r.get("lake_id"),
            "public_name": r.get("public_name"),
            "percent_util": r.get("percent_util"),
            "deviation_from_guide_curve_ft": r.get("deviation_from_guide_curve_ft"),
            "as_of": r.get("as_of"),
        }
        for r in ranked
    ]

    # Names per status bucket, so an agent can list lakes without re-deriving
    # (and miscounting or double-listing) them from the per-lake records.
    lakes_by_status: dict[str, list[str]] = {bucket: [] for bucket in counts}
    for r in in_scope:
        bucket = _status_bucket(r.get("pool_status", "no_data"))
        lakes_by_status[bucket].append(str(r.get("public_name")))

    filter_note = None
    if unevaluated:
        names = ", ".join(str(u["public_name"]) for u in unevaluated)
        filter_note = (
            f"{len(unevaluated)} lake(s) in scope could not be evaluated and are "
            f"NOT included in the filtered results: {names}. Do not report them "
            "as not matching the filter; say their status is unknown."
        )

    return {
        "as_of_utc": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "total_lakes": len(results),
        "counts": counts,
        "data_note": (
            "All status counts use today's live guide curve (Bottom of Flood Control); "
            "summer/winter pool are reference-only; always cite as_of_utc; "
            "report factually without operational judgments; "
            f"direct users to {LAKE_REPORT_URL} "
            "(LRL Daily Lake Report) for official information."
        ),
        "filters": {"basin": basin_value, "status": status_value},
        "lakes_in_scope": len(in_scope),
        "matched_lakes": len(filtered),
        "unevaluated_lakes": unevaluated,
        "filter_note": filter_note,
        "flood_storage_ranking": flood_storage_ranking,
        "lakes_by_status": lakes_by_status,
        "lakes": filtered,
    }


# ── Tool registration ─────────────────────────────────────────────────────────


def register(mcp: FastMCP) -> None:
    """Register the summarize_district_lakes tool with the MCP server."""
    mcp.tool(
        name="summarize_district_lakes",
        output_schema=output_schema(DistrictSummary),
        annotations={
            "title": "Summarize LRL District Lakes",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )(summarize_district_lakes)
