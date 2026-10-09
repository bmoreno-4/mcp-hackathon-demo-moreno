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

Pool status is classified relative to the live guide curve (Bottom of Flood
Control), not the static pools:
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

import asyncio
import datetime
from typing import Annotated, Any

from fastmcp import FastMCP

from lrl_reservoirs.guide_curve import interpolate_guide_curve
from lrl_reservoirs.lakes import LAKES, LakeName
from lrl_reservoirs.utils import LAKE_REPORT_URL, UpstreamServiceError, cwms_get

# ── Constants ─────────────────────────────────────────────────────────────────

OFFICE = "LRL"
# Look back up to 6 hours to find the most recent elevation value.
LOOKBACK_HOURS = 6

# Tolerance used for "at_guide" status (±0.05 ft ≈ reporting precision).
AT_GUIDE_TOLERANCE_FT = 0.05

# Standard one-line data note returned in every response dict.
_DATA_NOTE = (
    "guide_curve_ft is the operative seasonal target; "
    "summer/winter pool are reference-only; "
    "always cite as_of; report factually without operational judgments; "
    f"direct users to {LAKE_REPORT_URL} (LRL Daily Lake Report) "
    "for official information."
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


# ── CWMS fetch helpers ────────────────────────────────────────────────────────


async def _fetch_guide_curve(lake_id: str, at: datetime.datetime) -> float | None:
    """Fetch and interpolate the Bottom of Flood Control guide curve for *lake_id*
    at datetime *at*.

    Returns the elevation in feet, or None if the fetch fails or data is absent.
    """
    level_id = f"{lake_id}.Elev.Inst.0.Bottom of Flood Control"
    try:
        data = await cwms_get(
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

    Returns the latest non-null value in the lookback window, or None on any
    failure or missing data.
    """
    try:
        ts_data = await cwms_get(
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
        data = await cwms_get(
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
      - as_of (str | null): ISO-8601 UTC timestamp of the observation —
          ALWAYS include this when reporting conditions to users.
      - guide_curve_ft (float | null): Today's seasonal Bottom of Flood Control
          elevation — the operative target for the current date.  The LRL Daily
          Lake Report calls this "Pool" and measures "Dev. from Pool" against it.
          This is the primary reference for assessing whether the pool is
          high, low, or on target — NOT summer_pool or winter_pool.
      - deviation_from_guide_curve_ft (float | null): elevation minus guide_curve_ft;
          negative = pool is below today's guide curve
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
          winter_pool_ft, summer_pool_ft, flood_pool_ft.
          These are reference values only; do not use them to assess whether
          the pool is "below summer pool" or "below conservation pool" —
          deviations from these static targets are expected and normal during
          seasonal fill and drawdown operations.
      - data_note (str): One-line guidance reminding the caller that guide_curve_ft
          is the operative seasonal target; summer/winter pool are reference-only;
          always cite as_of; report factually without operational judgments; and
          direct users to the LRL Daily Lake Report for official information.
      - error (str): present only when the elevation API call failed

    Interpretation guidance for AI assistants:
      - guide_curve_ft (Bottom of Flood Control) is the operative seasonal target for
        the current date. Use it — not summer_pool or winter_pool — to assess whether
        the pool is high, low, or on target.
      - summer_pool_ft and winter_pool_ft are static reference values only. Do not
        describe a pool as "below summer pool" or "below conservation pool" as a
        shortfall; doing so is incorrect during drawdown or pre-fill periods.
      - Always report the observation timestamp (as_of) when stating pool conditions.
      - Report conditions factually (elevation, deviation, pool_status, percent_util).
        Do not make operational judgments or flood risk assessments such as "no
        concern" or "normal conditions".
      - For official lake conditions, direct users to the LRL Daily Lake Report:
        https://www.lrl-wc.usace.army.mil/reports/lkreport.html
    """
    lake_id: str = lake.value  # type: ignore[union-attr]
    meta = LAKES[lake_id]

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
        ts_data = await cwms_get(
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
            "data_note": _DATA_NOTE,
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
            "data_note": _DATA_NOTE,
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
        "data_note": _DATA_NOTE,
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
