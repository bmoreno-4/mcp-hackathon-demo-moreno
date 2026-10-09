"""Tool: get_lake_conditions

Returns the latest pool elevation for a USACE Louisville District (LRL)
reservoir, the today's seasonal guide curve elevation, and how the current
pool compares to both the guide curve and the static pool schedule.

Data source: CWMS Data API (https://cwms-data.usace.army.mil/cwms-data)
  - Elevation timeseries : <lake>.Elev.Inst.0.0.lrldlb-rev
  - Storage timeseries   : <lake>.Stor.Inst.1Hour.0.lrldlb-comp
                           (LRL Data Lab computed; lrldlb-rev is empty)
  - Guide curve (elev)   : <lake>.Elev.Inst.0.Bottom of Flood Control
                           (seasonal, interpolated; same as report "Dev. from Pool")
  - Storage at guide     : <lake>.Stor.Inst.0.Bottom of Flood Control
                           (seasonal; lower bound for Percent Util denominator)
  - Storage at flood     : <lake>.Stor.Inst.0.Top of Flood
                           (constant; upper bound for Percent Util denominator)
  - Vertical datum info  : embedded in the timeseries response
  - Update cadence       : typically every hour via lrldlb-comp.

What "501 Not Implemented" means:
  The CWMS Data API requires the header  Accept: application/json;version=2.
  Without it the server returns HTTP 501.  The follow_redirects setting has no
  effect; this endpoint does not issue redirects.

Static pool levels (winter_pool_ft, summer_pool_ft, flood_pool_ft) come from
the LRL Daily Lake Report 2026-10-08 and are baked into data/lrl_lakes.csv.
They change only with dam-pool schedule amendments.

Pool status is classified relative to the live guide curve, not the static
pools (which bound the normal operating range):
  - below_guide:       elevation < guide_curve_ft
  - at_guide:          elevation == guide_curve_ft  (within 0.05 ft)
  - above_guide:       guide_curve_ft < elevation < flood_pool_ft
  - at_or_above_flood: elevation >= flood_pool_ft
  - no_guide:          guide curve fetch failed; falls back to static pools
  - unknown:           flood_pool_ft missing

Percent Util (storage-based) matches the USACE LRL Daily Lake Report column.
Formula: (current_storage - storage_at_guide)
         / (storage_at_flood - storage_at_guide) * 100
  - Negative when pool is below guide curve.
  - Both bounds are fetched live from CWMS storage location levels.
"""

from __future__ import annotations

import calendar
import csv
import datetime
import importlib.resources
from enum import Enum
from typing import Annotated, Any

import httpx
from fastmcp import FastMCP

from lrl_reservoirs.utils import UpstreamServiceError

# ── Constants ─────────────────────────────────────────────────────────────────

CWMS_BASE = "https://cwms-data.usace.army.mil/cwms-data/"
OFFICE = "LRL"
# Look back up to 6 hours to find the most recent elevation value.
LOOKBACK_HOURS = 6
DEFAULT_TIMEOUT = 20.0
MAX_RESPONSE_BYTES = 1_000_000

# Tolerance used for "at_guide" status (±0.05 ft ≈ reporting precision).
AT_GUIDE_TOLERANCE_FT = 0.05


# ── Lookup table ──────────────────────────────────────────────────────────────


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


# ── Guide-curve interpolation (stdlib only — no dateutil) ─────────────────────


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

    The CWMS "Bottom of Flood Control" level is defined as a set of
    (offset-months, offset-minutes, value) anchors relative to an
    interval-origin that repeats every interval-months (always 12).
    interpolate-string="T" means linear interpolation between anchors.

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


# ── Pool-status helpers ───────────────────────────────────────────────────────


def _pool_status_vs_guide(
    elev: float,
    guide_curve: float,
    flood_pool: float | None,
) -> str:
    """Classify pool status relative to the live seasonal guide curve.

    Status values:
      below_guide       elevation is below today's guide curve
      at_guide          elevation is within AT_GUIDE_TOLERANCE_FT of guide curve
      above_guide       elevation is above guide curve but below flood pool
      at_or_above_flood elevation has reached or exceeded flood pool
      unknown           flood pool level is undefined
    """
    if flood_pool is None:
        return "unknown"
    if elev >= flood_pool:
        return "at_or_above_flood"
    if elev > guide_curve + AT_GUIDE_TOLERANCE_FT:
        return "above_guide"
    if elev < guide_curve - AT_GUIDE_TOLERANCE_FT:
        return "below_guide"
    return "at_guide"


def _pool_status(
    elev: float,
    top_conservation: float | None,
    top_normal: float | None,
    top_flood: float | None,
) -> str:
    """Classify pool status relative to static pool schedule (fallback).

    When top_conservation is None, conservation_ref == top_of_normal.
    'above_conservation' requires elev strictly above conservation_ref.
    """
    if top_normal is None or top_flood is None:
        return "unknown"
    conservation_ref = top_conservation if top_conservation is not None else top_normal
    if elev >= top_flood:
        return "at_or_above_flood"
    if elev > conservation_ref:
        return "above_conservation"
    if elev >= top_normal:
        return "normal"
    return "below_normal"


# ── HTTP helper ───────────────────────────────────────────────────────────────

# CWMS Data API requires this header to negotiate the v2 JSON response format.
# Without it (or with Accept: */*), the server returns HTTP 501 Not Implemented.
# The endpoint does not issue redirects; follow_redirects=False is correct.
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
        raise UpstreamServiceError(f"CWMS API returned status {status}.") from None
    except (
        httpx.DecodingError,
        __import__("json").JSONDecodeError,
        UnicodeDecodeError,
    ):
        raise UpstreamServiceError("CWMS API returned invalid JSON.") from None
    except httpx.RequestError:
        raise UpstreamServiceError("CWMS API request failed.") from None


async def _fetch_guide_curve(lake_id: str, at: datetime.datetime) -> float | None:
    """Fetch and interpolate the Bottom of Flood Control guide curve for *lake_id*
    at datetime *at*.

    Returns the elevation in feet, or None if the fetch fails or data is absent.
    """
    level_id = f"{lake_id}.Elev.Inst.0.Bottom of Flood Control"
    try:
        data = await _cwms_get(
            f"levels/{level_id}",
            params={
                "office": OFFICE,
                "unit": "ft",
                "effective-date": at.strftime("%Y-%m-%dT%H:%M:%S"),
            },
        )
    except UpstreamServiceError:
        return None

    if "constant-value" in data:
        return round(float(data["constant-value"]), 2)

    sv = data.get("seasonal-values")
    origin = data.get("interval-origin")
    months = data.get("interval-months")
    if not sv or not origin or not months:
        return None

    return interpolate_guide_curve(sv, origin, int(months), at)


async def _fetch_storage(stor_ts_id: str, begin: str, end: str) -> float | None:
    """Fetch the most recent storage observation (acre-feet) from *stor_ts_id*.

    Uses the lrldlb-comp (computed) timeseries — the lrldlb-rev series is
    present in the CWMS catalog but returns no values.

    Returns the latest non-null value in the lookback window, or None on any
    failure or missing data.
    """
    try:
        ts_data = await _cwms_get(
            "timeseries",
            params={
                "name": stor_ts_id,
                "office": OFFICE,
                "unit": "ac-ft",
                "begin": begin,
                "end": end,
            },
        )
    except UpstreamServiceError:
        return None

    values: list[list[Any]] = ts_data.get("values") or []
    if not values:
        return None
    last = values[-1]
    raw = last[1]
    if raw is None:
        return None
    return round(float(raw), 0)


async def _fetch_storage_level(
    lake_id: str, level_name: str, at: datetime.datetime
) -> float | None:
    """Fetch a CWMS storage location level (acre-feet) for *lake_id* at *at*.

    Handles both constant-value and seasonal (interpolated) levels.
    Used for 'Bottom of Flood Control' and 'Top of Flood' storage bounds.
    Returns None if the level is unavailable.
    """
    level_id = f"{lake_id}.Stor.Inst.0.{level_name}"
    try:
        data = await _cwms_get(
            f"levels/{level_id}",
            params={
                "office": OFFICE,
                "unit": "ac-ft",
                "effective-date": at.strftime("%Y-%m-%dT%H:%M:%S"),
            },
        )
    except UpstreamServiceError:
        return None

    if "constant-value" in data:
        return round(float(data["constant-value"]), 0)

    sv = data.get("seasonal-values")
    origin = data.get("interval-origin")
    months = data.get("interval-months")
    if not sv or not origin or not months:
        return None

    return round(interpolate_guide_curve(sv, origin, int(months), at), 0)


# ── Tool implementation ───────────────────────────────────────────────────────


async def get_lake_conditions(
    lake: Annotated[
        LakeName,  # type: ignore[valid-type]
        "CWMS location ID of the LRL reservoir (e.g. 'Barren', 'CaesarCreek').",
    ],
) -> dict[str, Any]:
    """Return the latest pool elevation for a USACE Louisville District reservoir,
    today's seasonal guide curve, and how the current pool compares to both.

    Data source: CWMS Data API lrldlb-comp timeseries (LRL Data Lab, computed).
    Observations are typically recorded every hour. The tool looks back up to
    6 hours to find the most recent value.

    Returns a dict with:
      - lake_id (str): CWMS location ID
      - public_name (str): Human-readable lake name
      - basin (str): River basin (e.g. "Salt River")
      - elevation_ft (float | null): Latest pool elevation in feet
      - vertical_datum (str | null): Datum of the elevation reading (e.g. "NGVD-29")
      - as_of (str | null): ISO-8601 UTC timestamp of the observation
      - guide_curve_ft (float | null): Today's seasonal Bottom of Flood Control
          elevation — the value the LRL Daily Lake Report calls "Pool" and
          measures "Dev. from Pool" against
      - deviation_from_guide_curve_ft (float | null): elevation minus guide_curve_ft;
          negative = pool is below guide curve
      - pool_status (str): Status relative to guide curve — one of:
          below_guide | at_guide | above_guide | at_or_above_flood | no_guide |
          unknown | no_data
          Falls back to static-pool classification (below_normal | normal |
          above_conservation | at_or_above_flood | unknown) when guide curve
          fetch fails (pool_status will be prefixed "no_guide/").
      - percent_to_flood_pool (float | null): Elevation-based position through
          the flood-control buffer. 0 % = at guide curve, 100 % = at flood pool.
          Negative = below guide curve.
      - storage_acre_ft (float | null): Current conservation pool storage in
          acre-feet (lrldlb-comp timeseries).
      - storage_at_guide_curve_acre_ft (float | null): Storage in acre-feet at
          today's guide curve elevation (Bottom of Flood Control storage level).
      - storage_at_flood_pool_acre_ft (float | null): Storage in acre-feet at
          the top of flood pool (Top of Flood storage level).
      - percent_util (float | null): Storage-based utilisation matching the
          "Percent Util" column in the USACE LRL Daily Lake Report.
          Formula: (storage - storage_at_guide)
              / (storage_at_flood - storage_at_guide) * 100.
          Negative when pool is below guide curve.
      - reference_levels (dict): Static pool schedule —
          winter_pool_ft, summer_pool_ft, flood_pool_ft
      - error (str): present only when the elevation API call failed
    """
    lake_id: str = lake.value  # type: ignore[union-attr]
    meta = _LAKES[lake_id]

    now_utc = datetime.datetime.now(datetime.timezone.utc)
    begin = (now_utc - datetime.timedelta(hours=LOOKBACK_HOURS)).strftime(
        "%Y-%m-%dT%H:%M:%S"
    )
    end = now_utc.strftime("%Y-%m-%dT%H:%M:%S")

    ref = {
        "winter_pool_ft": meta["top_of_normal_ft"],
        "summer_pool_ft": meta["top_of_conservation_ft"],
        "flood_pool_ft": meta["top_of_flood_ft"],
    }

    # ── 1. Fetch current elevation ─────────────────────────────────────────────
    try:
        ts_data = await _cwms_get(
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
            "basin": meta["basin"],
            "elevation_ft": None,
            "vertical_datum": None,
            "as_of": None,
            "guide_curve_ft": None,
            "deviation_from_guide_curve_ft": None,
            "pool_status": "no_data",
            "percent_to_flood_pool": None,
            "storage_acre_ft": None,
            "storage_at_guide_curve_acre_ft": None,
            "storage_at_flood_pool_acre_ft": None,
            "percent_util": None,
            "reference_levels": ref,
            "error": str(exc),
        }

    values: list[list[Any]] = ts_data.get("values") or []
    if not values:
        return {
            "lake_id": lake_id,
            "public_name": meta["public_name"],
            "basin": meta["basin"],
            "elevation_ft": None,
            "vertical_datum": None,
            "as_of": None,
            "guide_curve_ft": None,
            "deviation_from_guide_curve_ft": None,
            "pool_status": "no_data",
            "percent_to_flood_pool": None,
            "storage_acre_ft": None,
            "storage_at_guide_curve_acre_ft": None,
            "storage_at_flood_pool_acre_ft": None,
            "percent_util": None,
            "reference_levels": ref,
            "error": "No observations returned for the lookback window.",
        }

    # values rows are [timestamp_ms, value, quality_code]; take the last row.
    last = values[-1]
    elev_ft: float = round(float(last[1]), 2)
    ts_ms: int = int(last[0])
    obs_utc = datetime.datetime.fromtimestamp(ts_ms / 1000, tz=datetime.timezone.utc)
    as_of = obs_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

    # Extract vertical datum from the timeseries response.
    vd_info = ts_data.get("vertical-datum-info") or {}
    vertical_datum: str | None = vd_info.get("native-datum") or None

    # ── 2. Fetch guide curve, storage, and storage bounds in parallel ─────────
    import asyncio

    guide_ft, storage_af, stor_at_guide, stor_at_flood = await asyncio.gather(
        _fetch_guide_curve(lake_id, obs_utc),
        _fetch_storage(meta["stor_ts_id"], begin, end),
        _fetch_storage_level(lake_id, "Bottom of Flood Control", obs_utc),
        _fetch_storage_level(lake_id, "Top of Flood", obs_utc),
    )

    # ── 3. Derive status and deviation ───────────────────────────────────────
    flood_pool = meta["top_of_flood_ft"]

    if guide_ft is not None:
        pool_status = _pool_status_vs_guide(elev_ft, guide_ft, flood_pool)
        deviation = round(elev_ft - guide_ft, 2)
        if flood_pool is not None:
            buffer = flood_pool - guide_ft
            pct = round((elev_ft - guide_ft) / buffer * 100, 1) if buffer != 0 else None
        else:
            pct = None
    else:
        # Guide curve unavailable — fall back to static pool classification.
        static_status = _pool_status(
            elev_ft,
            meta["top_of_conservation_ft"],
            meta["top_of_normal_ft"],
            flood_pool,
        )
        pool_status = f"no_guide/{static_status}"
        deviation = None
        pct = None

    # ── 4. Compute storage-based Percent Util (matches USACE LRL report) ──────
    # Formula: (cur - gc_stor) / (flood_stor - gc_stor) * 100
    # Both bounds come from live CWMS storage location levels.
    if (
        storage_af is not None
        and stor_at_guide is not None
        and stor_at_flood is not None
        and stor_at_flood != stor_at_guide
    ):
        percent_util: float | None = round(
            (storage_af - stor_at_guide) / (stor_at_flood - stor_at_guide) * 100, 1
        )
    else:
        percent_util = None

    return {
        "lake_id": lake_id,
        "public_name": meta["public_name"],
        "basin": meta["basin"],
        "elevation_ft": elev_ft,
        "vertical_datum": vertical_datum,
        "as_of": as_of,
        "guide_curve_ft": guide_ft,
        "deviation_from_guide_curve_ft": deviation,
        "pool_status": pool_status,
        "percent_to_flood_pool": pct,
        "storage_acre_ft": storage_af,
        "storage_at_guide_curve_acre_ft": stor_at_guide,
        "storage_at_flood_pool_acre_ft": stor_at_flood,
        "percent_util": percent_util,
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
