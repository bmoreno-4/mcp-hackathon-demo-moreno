"""Tests for the summarize_district_lakes tool."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastmcp import FastMCP

from lrl_reservoirs.tools import summarize_district_lakes as sdl_mod
from lrl_reservoirs.tools.lake_conditions import _LAKES

# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_lake_result(
    lake_id: str,
    pool_status: str = "above_guide",
    elevation_ft: float = 550.0,
    guide_curve_ft: float = 548.0,
    percent_util: float | None = 1.2,
    error: str | None = None,
) -> dict:
    """Build a minimal get_lake_conditions-style result dict for mocking."""
    meta = _LAKES[lake_id]
    result: dict = {
        "lake_id": lake_id,
        "public_name": meta["public_name"],
        "basin": meta["basin"],
        "elevation_ft": elevation_ft,
        "vertical_datum": "NGVD-29",
        "as_of": "2026-10-09T11:00:00Z",
        "guide_curve_ft": guide_curve_ft,
        "deviation_from_guide_curve_ft": round(elevation_ft - guide_curve_ft, 2),
        "pool_status": pool_status,
        "percent_to_flood_pool": 1.6,
        "storage_acre_ft": 237000.0,
        "storage_at_guide_curve_acre_ft": 232000.0,
        "storage_at_flood_pool_acre_ft": 873000.0,
        "percent_util": percent_util,
        "reference_levels": {
            "winter_pool_ft": meta["top_of_normal_ft"],
            "summer_pool_ft": meta["top_of_conservation_ft"],
            "flood_pool_ft": meta["top_of_flood_ft"],
        },
    }
    if error is not None:
        result["error"] = error
        result["pool_status"] = "no_data"
        result["elevation_ft"] = None
    return result


# ── Tests ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_summarize_returns_all_17_lakes():
    """Result contains an entry for every lake in the district."""
    fake_results = [_make_lake_result(lid) for lid in _LAKES]

    with patch.object(
        sdl_mod, "get_lake_conditions",
        new=AsyncMock(side_effect=fake_results),
    ):
        result = await sdl_mod.summarize_district_lakes()

    assert result["total_lakes"] == 17
    assert len(result["lakes"]) == 17


@pytest.mark.asyncio
async def test_summarize_counts_are_correct():
    """Aggregate counts reflect the mocked pool statuses."""
    statuses = (
        ["above_guide"] * 5
        + ["at_guide"] * 3
        + ["below_guide"] * 4
        + ["at_or_above_flood"] * 1
        + ["no_data"] * 4
    )
    lake_ids = list(_LAKES.keys())
    fake_results = [
        _make_lake_result(lid, pool_status=st)
        for lid, st in zip(lake_ids, statuses)
    ]

    with patch.object(
        sdl_mod, "get_lake_conditions",
        new=AsyncMock(side_effect=fake_results),
    ):
        result = await sdl_mod.summarize_district_lakes()

    assert result["counts"]["above_guide"] == 5
    assert result["counts"]["at_guide"] == 3
    assert result["counts"]["below_guide"] == 4
    assert result["counts"]["at_or_above_flood"] == 1
    assert result["counts"]["no_data"] == 4
    assert sum(result["counts"].values()) == 17


@pytest.mark.asyncio
async def test_summarize_sorted_by_basin_then_name():
    """Lakes list is sorted by basin then public_name."""
    fake_results = [_make_lake_result(lid) for lid in _LAKES]

    with patch.object(
        sdl_mod, "get_lake_conditions",
        new=AsyncMock(side_effect=fake_results),
    ):
        result = await sdl_mod.summarize_district_lakes()

    lakes = result["lakes"]
    pairs = [(r["basin"], r["public_name"]) for r in lakes]
    assert pairs == sorted(pairs), "Lakes are not sorted by basin then public_name"


@pytest.mark.asyncio
async def test_summarize_includes_error_lakes_in_no_data_count():
    """Lakes with errors are included in the list and counted as no_data."""
    lake_ids = list(_LAKES.keys())
    fake_results = [
        _make_lake_result(lid, error="CWMS API returned status 503.") if i == 0
        else _make_lake_result(lid, pool_status="above_guide")
        for i, lid in enumerate(lake_ids)
    ]

    with patch.object(
        sdl_mod, "get_lake_conditions",
        new=AsyncMock(side_effect=fake_results),
    ):
        result = await sdl_mod.summarize_district_lakes()

    assert result["counts"]["no_data"] == 1
    assert result["counts"]["above_guide"] == 16
    # The errored lake is still present in the list
    error_lakes = [r for r in result["lakes"] if "error" in r]
    assert len(error_lakes) == 1


@pytest.mark.asyncio
async def test_summarize_has_as_of_utc_field():
    """Result includes a top-level as_of_utc ISO timestamp."""
    fake_results = [_make_lake_result(lid) for lid in _LAKES]

    with patch.object(
        sdl_mod, "get_lake_conditions",
        new=AsyncMock(side_effect=fake_results),
    ):
        result = await sdl_mod.summarize_district_lakes()

    assert "as_of_utc" in result
    # Basic ISO format check: YYYY-MM-DDTHH:MM:SSZ
    assert result["as_of_utc"].endswith("Z")
    assert "T" in result["as_of_utc"]


@pytest.mark.asyncio
async def test_summarize_tool_is_discoverable():
    """summarize_district_lakes is registered and visible in the tool list."""
    mcp = FastMCP("test")
    sdl_mod.register(mcp)
    tools = {t.name: t for t in await mcp.list_tools()}
    assert "summarize_district_lakes" in tools
