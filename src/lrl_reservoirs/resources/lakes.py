"""Resource: lrl://lakes

Static directory of every USACE Louisville District (LRL) reservoir:
CWMS location ID, public name, basin, and static pool elevations.
"""

from __future__ import annotations

from fastmcp import FastMCP

from lrl_reservoirs.lakes import LAKES


def register(mcp: FastMCP) -> None:
    """Register the lrl://lakes resource with the MCP server."""

    @mcp.resource(
        uri="lrl://lakes",
        name="lrl_lakes_directory",
        description=(
            "Directory of all 17 USACE Louisville District (LRL) reservoirs. "
            "Each entry lists the CWMS location ID (lake_id), human-readable public "
            "name, river basin, and static pool elevations (winter_pool_ft, "
            "summer_pool_ft, flood_pool_ft). Use this to resolve a human name such as "
            "'Harsha Lake' or 'Barren River Lake' to its lake_id before calling "
            "get_lake_conditions."
        ),
    )
    def lrl_lakes_directory() -> list[dict]:
        return [
            {
                "lake_id": meta["lake_id"],
                "public_name": meta["public_name"],
                "basin": meta["basin"],
                "winter_pool_ft": meta["top_of_normal_ft"],
                "summer_pool_ft": meta["top_of_conservation_ft"],
                "flood_pool_ft": meta["top_of_flood_ft"],
            }
            for meta in LAKES.values()
        ]
