"""Tool: list_lakes

Static reference directory of the 17 USACE Louisville District (LRL) flood risk
management lakes: CWMS lake_id, official name, basin, and the pool schedule
(winter pool, summer pool, top of flood pool). No network calls.

This mirrors the lrl://lakes MCP resource as a tool, because many agent
platforms (e.g. watsonx Orchestrate) only expose tools to the model.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastmcp import FastMCP

from lrl_reservoirs.lakes import LAKES
from lrl_reservoirs.tools.summarize_district_lakes import BasinName
from lrl_reservoirs.utils import LAKE_REPORT_URL


async def list_lakes(
    basin: Annotated[
        BasinName | None,  # type: ignore[valid-type]
        "Optional: only list lakes in this basin (same values as "
        "summarize_district_lakes).",
    ] = None,
) -> dict[str, Any]:
    """List LRL lakes with their official names, basins and pool schedule.

    Use this tool to answer reference questions such as "What are the summer and
    winter pools for Brookville?", "What is the flood pool at Nolin?", "Which
    basin is C. M. Harden Lake in?" or "Which lakes does the Louisville District
    operate?". Report these values directly when asked.

    These are static schedule values, not live conditions. For current pool
    elevation, guide curve or flood storage use get_lake_conditions or
    summarize_district_lakes. Winter/summer pool are the seasonal targets; the
    guide curve moves between them during fill and drawdown, so do not describe
    a lake as "below summer pool" as a shortfall.

    Returns a dict with:
      - lakes (list): lake_id, public_name, basin, winter_pool_ft,
          summer_pool_ft, flood_pool_ft (feet, NGVD-29).
      - source (str): where the pool schedule comes from.
    """
    basin_value = basin.value if basin is not None else None  # type: ignore[union-attr]
    lakes = [
        {
            "lake_id": meta["lake_id"],
            "public_name": meta["public_name"],
            "basin": meta["basin"],
            "winter_pool_ft": meta["top_of_normal_ft"],
            "summer_pool_ft": meta["top_of_conservation_ft"],
            "flood_pool_ft": meta["top_of_flood_ft"],
        }
        for meta in LAKES.values()
        if basin_value is None or meta["basin"] == basin_value
    ]
    lakes.sort(key=lambda r: (r["basin"], r["public_name"]))
    return {
        "lakes": lakes,
        "vertical_datum": "NGVD-29",
        "source": f"LRL Daily Lake Report pool schedule ({LAKE_REPORT_URL})",
    }


def register(mcp: FastMCP) -> None:
    """Register the list_lakes tool with the MCP server."""
    mcp.tool(
        name="list_lakes",
        annotations={
            "title": "List LRL Lakes (reference)",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )(list_lakes)
