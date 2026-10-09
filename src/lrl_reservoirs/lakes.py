"""Lake metadata table and LakeName enum for the LRL district.

Loads the LRL lakes CSV shipped with the package and exposes:
  - LAKES: dict mapping lake_id → metadata dict
  - LakeName: dynamic Enum (one member per lake), suitable as a FastMCP parameter type

Static pool levels (winter_pool_ft, summer_pool_ft, flood_pool_ft) come from
the LRL Daily Lake Report 2026-10-08 and are baked into data/lrl_lakes.csv.
They change only with dam-pool schedule amendments and are REFERENCE values only.
"""

from __future__ import annotations

import csv
import importlib.resources
from enum import Enum
from typing import Any


def _load_lake_table() -> dict[str, dict[str, Any]]:
    """Load the LRL lakes CSV shipped with the package into a keyed dict."""
    table: dict[str, dict[str, Any]] = {}
    pkg = importlib.resources.files("lrl_reservoirs").joinpath("data/lrl_lakes.csv")
    with importlib.resources.as_file(pkg) as path:
        with open(path, newline="") as fh:
            for row in csv.DictReader(fh):
                lid = row["lake_id"]
                table[lid] = {
                    "lake_id": lid,
                    "public_name": row["public_name"],
                    "basin": row["basin"],
                    "elev_ts_id": row["elev_ts_id"],
                    "stor_ts_id": row["stor_ts_id"],
                    "inflow_ts_id": row["inflow_ts_id"],
                    "outflow_ts_id": row["outflow_ts_id"],
                    # CSV columns from the LRL Daily Lake Report (2026-10-08):
                    #   winter_pool_ft  → maps to internal top_of_normal_ft
                    #   summer_pool_ft  → maps to internal top_of_conservation_ft
                    #   flood_pool_ft   → maps to internal top_of_flood_ft
                    "top_of_conservation_ft": float(row["summer_pool_ft"])
                    if row["summer_pool_ft"]
                    else None,
                    "top_of_normal_ft": float(row["winter_pool_ft"])
                    if row["winter_pool_ft"]
                    else None,
                    "top_of_flood_ft": float(row["flood_pool_ft"])
                    if row["flood_pool_ft"]
                    else None,
                }
    return table


LAKES: dict[str, dict[str, Any]] = _load_lake_table()

# Build the public-name mapping string for the LakeName docstring so agents can
# resolve human names (e.g. "Harsha Lake") to CWMS location IDs.
_PUBLIC_NAME_LIST = ", ".join(
    f"{lid}={meta['public_name']}" for lid, meta in LAKES.items()
)

LakeName = Enum(  # type: ignore[misc]
    "LakeName",
    {lid: lid for lid in LAKES},
    type=str,
)
LakeName.__doc__ = (
    "USACE Louisville District (LRL) reservoir CWMS location ID. "
    "Each value is the CWMS location ID used in timeseries names. "
    f"Public names: {_PUBLIC_NAME_LIST}."
)
