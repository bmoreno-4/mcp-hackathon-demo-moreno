"""Tool: get_lake_conditions

Returns the latest pool elevation for a USACE Louisville District (LRL)
reservoir and how it compares to reference pool levels (Top of Conservation,
Top of Normal, Top of Flood).

Data source: CWMS Data API (https://cwms-data.usace.army.mil/cwms-data)
  - Timeseries: <lake>.Elev.Inst.0.0.lrldlb-rev  (lrldlb = LRL Data Lab revised)
  - Update cadence: typically every 15–60 minutes; may lag by 1–2 hours.

Reference pool levels are baked into the package lookup table
(data/lrl_lakes.csv) — they change only with dam pool schedule amendments
and are not fetched at runtime.

Pool status definitions:
  - below_normal:        elevation < top_of_normal
  - normal:              top_of_normal <= elevation < top_of_conservation
  - above_conservation:  top_of_conservation <= elevation < top_of_flood
    (pool is in the flood-control buffer — actively storing flood water)
  - at_or_above_flood:   elevation >= top_of_flood
    (pool has reached or exceeded the top of flood pool — extreme event)
  - unknown:             no conservation level defined for this lake
    (Patoka Lake does not have a Top of Conservation; status uses Top of Normal)
"""

from __future__ import annotations

import csv
import datetime
import importlib.resources
from enum import Enum
from typing import Annotated, Any

import httpx
from fastmcp import FastMCP

from example_server.utils import UpstreamServiceError

# ── Constants ─────────────────────────────────────────────────────────────────

CWMS_BASE = "https://cwms-data.usace.army.mil/cwms-data/"
OFFICE = "LRL"
# Look back up to 6 hours to find the most recent value, then take the last one.
LOOKBACK_HOURS = 6
DEFAULT_TIMEOUT = 20.0
MAX_RESPONSE_BYTES = 1_000_000


# ── Lookup table ──────────────────────────────────────────────────────────────

def _load_lake_table() -> dict[str, dict[str, Any]]:
    """Load the LRL lakes CSV shipped with the package into a keyed dict."""
    table: dict[str, dict[str, Any]] = {}
    pkg = importlib.resources.files("example_server").joinpath("data/lrl_lakes.csv")
    with importlib.resources.as_file(pkg) as path:
        with open(path, newline="") as fh:
            for row in csv.DictReader(fh):
                lid = row["lake_id"]
                table[lid] = {
                    "lake_id": lid,
                    "public_name": row["public_name"],
                    "elev_ts_id": row["elev_ts_id"],
                    "stor_ts_id": row["stor_ts_id"],
                    "inflow_ts_id": row["inflow_ts_id"],
                    "outflow_ts_id": row["outflow_ts_id"],
                    "top_of_conservation_ft": float(row["top_of_conservation_ft"])
                    if row["top_of_conservation_ft"]
                    else None,
                    "top_of_normal_ft": float(row["top_of_normal_ft"])
                    if row["top_of_normal_ft"]
                    else None,
                    "top_of_flood_ft": float(row["top_of_flood_ft"])
                    if row["top_of_flood_ft"]
                    else None,
                }
    return table


_LAKES: dict[str, dict[str, Any]] = _load_lake_table()


# ── Enum (one member per lake, validated at call time) ────────────────────────

LakeName = Enum(  # type: ignore[misc]
    "LakeName",
    {lid: lid for lid in _LAKES},
    type=str,
)
LakeName.__doc__ = (
    "USACE Louisville District (LRL) reservoir project identifier. "
    "Each value is the CWMS location ID used in timeseries names."
)


# ── Pool-status derivation ────────────────────────────────────────────────────

def _pool_status(
    elev: float,
    top_conservation: float | None,
    top_normal: float | None,
    top_flood: float | None,
) -> str:
    """Return a human-readable pool status string.

    When top_conservation is None (e.g. Patoka), the conservation pool is
    considered coincident with normal pool, so the above_conservation band
    starts strictly above top_of_normal.
    """
    if top_normal is None or top_flood is None:
        return "unknown"
    # Use conservation level if defined; otherwise normal pool is the upper
    # bound of "normal" status and any elevation above it is above_conservation.
    conservation_ref = top_conservation if top_conservation is not None else top_normal
    if elev >= top_flood:
        return "at_or_above_flood"
    if elev > conservation_ref:
        return "above_conservation"
    if elev >= top_normal:
        return "normal"
    return "below_normal"


# ── HTTP helper (CWMS-specific — does not reuse utils.fetch_json whose
#    base URL is pinned to the example.gov origin) ────────────────────────────

# CWMS Data API requires this header to negotiate the v2 JSON response format.
# Without it (or with Accept: */*), the server returns HTTP 501 Not Implemented.
CWMS_ACCEPT = "application/json;version=2"


async def _cwms_get(path: str, params: dict[str, str]) -> Any:
    """GET a CWMS Data API path and return parsed JSON.

    path must be a relative path segment (no leading slash, no scheme/host).
    Raises UpstreamServiceError with a sanitized message on any failure.
    """
    import json

    try:
        async with httpx.AsyncClient(
            base_url=CWMS_BASE,
            follow_redirects=False,
            timeout=DEFAULT_TIMEOUT,
            trust_env=False,
            headers={"Accept": CWMS_ACCEPT},
        ) as client:
            async with client.stream("GET", path, params=params) as response:
                response.raise_for_status()
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > MAX_RESPONSE_BYTES:
                        raise UpstreamServiceError(
                            "Upstream response exceeded the size limit."
                        )
                return json.loads(content)
    except UpstreamServiceError:
        raise
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status == 501:
            raise UpstreamServiceError(
                f"CWMS API returned status {status}: request format not accepted "
                f"(check Accept header and query parameters)."
            ) from None
        raise UpstreamServiceError(
            f"CWMS API returned status {status}."
        ) from None
    except (
        httpx.DecodingError,
        __import__("json").JSONDecodeError,
        UnicodeDecodeError,
    ):
        raise UpstreamServiceError("CWMS API returned invalid JSON.") from None
    except httpx.RequestError:
        raise UpstreamServiceError("CWMS API request failed.") from None


# ── Tool implementation (module-level so tests can import and call it directly)

async def get_lake_conditions(
    lake: Annotated[
        LakeName,  # type: ignore[valid-type]
        "CWMS location ID of the LRL reservoir (e.g. 'Barren', 'CaesarCreek').",
    ],
) -> dict[str, Any]:
    """Return the latest pool elevation for a USACE Louisville District reservoir
    and how it compares to reference pool levels.

    Data source: CWMS Data API lrldlb-rev timeseries (LRL Data Lab, revised).
    Observations are typically recorded every 15–60 minutes. The tool looks
    back up to 6 hours to find the most recent value.

    Returns a dict with:
      - lake_id (str): CWMS location ID
      - public_name (str): Human-readable lake name
      - elevation_ft (float | null): Latest pool elevation in feet (NGVD-29)
      - as_of (str | null): ISO-8601 UTC timestamp of the observation
      - pool_status (str): One of below_normal | normal | above_conservation |
          at_or_above_flood | unknown | no_data
      - deviation_from_conservation_ft (float | null): elevation minus
          top_of_conservation_ft; negative = below conservation pool
      - reference_levels (dict): top_of_normal_ft, top_of_conservation_ft,
          top_of_flood_ft as defined in the LRL pool schedule
      - error (str): present only when the API call failed
    """
    lake_id: str = lake.value  # type: ignore[union-attr]
    meta = _LAKES[lake_id]

    now_utc = datetime.datetime.now(datetime.timezone.utc)
    begin = (now_utc - datetime.timedelta(hours=LOOKBACK_HOURS)).strftime(
        "%Y-%m-%dT%H:%M:%S"
    )
    end = now_utc.strftime("%Y-%m-%dT%H:%M:%S")

    ref = {
        "top_of_normal_ft": meta["top_of_normal_ft"],
        "top_of_conservation_ft": meta["top_of_conservation_ft"],
        "top_of_flood_ft": meta["top_of_flood_ft"],
    }

    try:
        data = await _cwms_get(
            "timeseries",
            params={
                "name": meta["elev_ts_id"],
                "office": OFFICE,
                "unit": "ft",
                "begin": begin,
                "end": end,
            },
        )
    except UpstreamServiceError as exc:
        return {
            "lake_id": lake_id,
            "public_name": meta["public_name"],
            "elevation_ft": None,
            "as_of": None,
            "pool_status": "no_data",
            "deviation_from_conservation_ft": None,
            "reference_levels": ref,
            "error": str(exc),
        }

    values: list[list[Any]] = data.get("values") or []
    if not values:
        return {
            "lake_id": lake_id,
            "public_name": meta["public_name"],
            "elevation_ft": None,
            "as_of": None,
            "pool_status": "no_data",
            "deviation_from_conservation_ft": None,
            "reference_levels": ref,
            "error": "No observations returned for the lookback window.",
        }

    # values rows are [timestamp_ms, value, quality_code]; take the last row.
    last = values[-1]
    elev_ft: float = round(float(last[1]), 2)
    ts_ms: int = int(last[0])
    as_of = datetime.datetime.fromtimestamp(
        ts_ms / 1000, tz=datetime.timezone.utc
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    status = _pool_status(
        elev_ft,
        meta["top_of_conservation_ft"],
        meta["top_of_normal_ft"],
        meta["top_of_flood_ft"],
    )

    conservation_ref = (
        meta["top_of_conservation_ft"]
        if meta["top_of_conservation_ft"] is not None
        else meta["top_of_normal_ft"]
    )
    deviation = (
        round(elev_ft - conservation_ref, 2) if conservation_ref is not None else None
    )

    return {
        "lake_id": lake_id,
        "public_name": meta["public_name"],
        "elevation_ft": elev_ft,
        "as_of": as_of,
        "pool_status": status,
        "deviation_from_conservation_ft": deviation,
        "reference_levels": ref,
    }


# ── Tool registration ─────────────────────────────────────────────────────────

def register(mcp: FastMCP) -> None:
    """Register the get_lake_conditions tool with the MCP server."""
    mcp.tool(
        name="get_lake_conditions",
        annotations={
            "title": "Get LRL Lake Conditions",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )(get_lake_conditions)
