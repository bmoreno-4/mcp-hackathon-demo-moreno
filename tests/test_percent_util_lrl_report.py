"""Deterministic percent_util validation against the LRL Daily Lake Report 2026-10-08.

Uses saved CWMS API responses captured at the report time (2026-10-08 06:00 US/Eastern
= 10:00 UTC) to verify that get_lake_conditions computes percent_util within ±0.15
of the Percent Util column in the LRL Daily Lake Report for all 17 lakes.

Fixture: tests/fixtures/lrl_oct8_2026/cwms_responses.json
  Created by fetching real CWMS data for the report window 08:00–12:00 UTC on
  2026-10-08 (±2 h around 10:00 UTC).  Re-run eval/lrl_percent_util_validation.py
  with --save-fixtures to refresh it.

Call order that _cwms_get sees per lake (must match the implementation in
lake_conditions.py):
  1. elev timeseries           (sequential, before asyncio.gather)
  asyncio.gather fires 4 concurrent calls in registration order:
  2. elev guide curve level    (_fetch_guide_curve)
  3. storage timeseries        (_fetch_storage)
  4. stor @ guide curve level  (_fetch_storage_level "Bottom of Flood Control")
  5. stor @ flood pool level   (_fetch_storage_level "Top of Flood")
"""

from __future__ import annotations

import json
import pathlib
from unittest.mock import AsyncMock, patch

import pytest

from example_server.tools import lake_conditions as lc_mod
from example_server.tools.lake_conditions import LakeName

# ── Fixtures ──────────────────────────────────────────────────────────────────

FIXTURE_PATH = (
    pathlib.Path(__file__).parent / "fixtures" / "lrl_oct8_2026" / "cwms_responses.json"
)

# Report Percent Util values from lrl_lake_report_2026-10-08.txt
REPORT_PERCENT_UTIL = {
    "CaesarCreek":   0.05,
    "WHHarsha":     -0.05,
    "WestFork":      0.24,
    "CJBrown":       0.79,
    "Brookville":    0.48,
    "CaveRun":       2.62,
    "CarrCreek":     7.20,
    "Buckhorn":      0.94,
    "Taylorsville": -0.68,
    "Green":        -0.85,
    "Nolin":         1.33,
    "Barren":        0.95,
    "Rough":        -0.18,
    "CMHarden":     -0.10,
    "CaglesMill":    0.18,
    "Monroe":       -0.37,
    "Patoka":       21.77,
}

# Maximum allowed absolute difference from the report value.
# The report rounds to 2 decimal places and the storage TS timestamps may differ
# from the exact report time by up to one hour, so we allow ±0.15.
TOLERANCE = 0.15


@pytest.fixture(scope="module")
def fixtures() -> dict:
    with open(FIXTURE_PATH) as f:
        return json.load(f)


# ── Parametrised test — one case per lake ─────────────────────────────────────

@pytest.mark.parametrize("lake_id", list(REPORT_PERCENT_UTIL.keys()))
@pytest.mark.asyncio
async def test_percent_util_matches_lrl_report(lake_id: str, fixtures: dict):
    """get_lake_conditions percent_util matches the LRL report within ±0.10 for *lake_id*.

    The mock replays the five _cwms_get calls in the order the implementation
    issues them:
      1. elevation timeseries
      2–5. (parallel) elev guide curve, storage timeseries,
            storage-at-guide level, storage-at-flood level
    """
    data = fixtures[lake_id]

    side_effects = [
        data["elev_ts_response"],
        data["elev_gc_level_response"],   # guide curve elevation (parallel slot 1)
        data["stor_ts_response"],          # storage timeseries   (parallel slot 2)
        data["gc_level_response"],         # stor @ guide curve   (parallel slot 3)
        data["flood_level_response"],      # stor @ flood pool    (parallel slot 4)
    ]

    with patch.object(
        lc_mod, "_cwms_get", new=AsyncMock(side_effect=side_effects)
    ):
        result = await lc_mod.get_lake_conditions(lake=LakeName(lake_id))  # type: ignore[call-arg]

    report_pct = REPORT_PERCENT_UTIL[lake_id]
    calc_pct   = result.get("percent_util")

    assert calc_pct is not None, (
        f"{lake_id}: percent_util is None — "
        f"storage_acre_ft={result.get('storage_acre_ft')}, "
        f"storage_at_guide_curve_acre_ft={result.get('storage_at_guide_curve_acre_ft')}, "
        f"storage_at_flood_pool_acre_ft={result.get('storage_at_flood_pool_acre_ft')}"
    )
    assert abs(calc_pct - report_pct) <= TOLERANCE, (
        f"{lake_id}: percent_util={calc_pct:.2f} vs report={report_pct:.2f} "
        f"(Δ={calc_pct - report_pct:+.2f}, tolerance=±{TOLERANCE})"
    )
