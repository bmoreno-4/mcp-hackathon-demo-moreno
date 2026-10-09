"""Tests for the summarize_district_lakes tool."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastmcp import FastMCP

from lrl_reservoirs.lakes import LAKES
from lrl_reservoirs.tools import summarize_district_lakes as sdl_mod

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
    meta = LAKES[lake_id]
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
    fake_results = [_make_lake_result(lid) for lid in LAKES]

    with patch.object(
        sdl_mod,
        "get_lake_conditions",
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
    lake_ids = list(LAKES.keys())
    fake_results = [
        _make_lake_result(lid, pool_status=st) for lid, st in zip(lake_ids, statuses)
    ]

    with patch.object(
        sdl_mod,
        "get_lake_conditions",
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
    fake_results = [_make_lake_result(lid) for lid in LAKES]

    with patch.object(
        sdl_mod,
        "get_lake_conditions",
        new=AsyncMock(side_effect=fake_results),
    ):
        result = await sdl_mod.summarize_district_lakes()

    lakes = result["lakes"]
    pairs = [(r["basin"], r["public_name"]) for r in lakes]
    assert pairs == sorted(pairs), "Lakes are not sorted by basin then public_name"


@pytest.mark.asyncio
async def test_summarize_includes_error_lakes_in_no_data_count():
    """Lakes with errors are included in the list and counted as no_data."""
    lake_ids = list(LAKES.keys())
    fake_results = [
        _make_lake_result(lid, error="CWMS API returned status 503.")
        if i == 0
        else _make_lake_result(lid, pool_status="above_guide")
        for i, lid in enumerate(lake_ids)
    ]

    with patch.object(
        sdl_mod,
        "get_lake_conditions",
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
    fake_results = [_make_lake_result(lid) for lid in LAKES]

    with patch.object(
        sdl_mod,
        "get_lake_conditions",
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


# ── Filter tests (regression: wxO answered "none above guide" for Kentucky) ──


def _mock_by_lake(statuses: dict[str, str], errors: dict[str, str] | None = None):
    """AsyncMock whose result depends on the lake requested (default below_guide)."""
    errors = errors or {}

    async def _fake(lake):
        lid = lake.value
        if lid in errors:
            return _make_lake_result(lid, error=errors[lid], percent_util=None)
        return _make_lake_result(lid, pool_status=statuses.get(lid, "below_guide"))

    return AsyncMock(side_effect=_fake)


@pytest.mark.asyncio
async def test_kentucky_basin_above_guide_returns_both_lakes():
    """LRL report 2026-10-09: Carr Creek +3.1 ft and Buckhorn +1.6 ft above guide."""
    mock = _mock_by_lake({"CarrCreek": "above_guide", "Buckhorn": "above_guide"})
    with patch.object(sdl_mod, "get_lake_conditions", new=mock):
        result = await sdl_mod.summarize_district_lakes(
            basin=sdl_mod.BasinName("Kentucky"),
            status=sdl_mod.PoolStatusFilter("above_guide"),
        )

    assert {r["lake_id"] for r in result["lakes"]} == {"CarrCreek", "Buckhorn"}
    assert result["lakes_in_scope"] == 2
    assert result["matched_lakes"] == 2
    assert result["unevaluated_lakes"] == []
    assert result["filter_note"] is None
    assert result["filters"] == {"basin": "Kentucky", "status": "above_guide"}


@pytest.mark.asyncio
async def test_basin_filter_only_returns_basin_lakes():
    mock = _mock_by_lake({})
    with patch.object(sdl_mod, "get_lake_conditions", new=mock):
        result = await sdl_mod.summarize_district_lakes(
            basin=sdl_mod.BasinName("Green River")
        )

    assert {r["lake_id"] for r in result["lakes"]} == {
        "Barren",
        "Green",
        "Nolin",
        "Rough",
    }
    assert result["total_lakes"] == 17  # counts still cover the whole district


@pytest.mark.asyncio
async def test_status_filter_reports_unevaluated_lakes_instead_of_dropping():
    """A lake with no data must be reported as unknown, never as 'not matching'."""
    mock = _mock_by_lake(
        {"Buckhorn": "above_guide"},
        errors={"CarrCreek": "CWMS request timed out."},
    )
    with patch.object(sdl_mod, "get_lake_conditions", new=mock):
        result = await sdl_mod.summarize_district_lakes(
            basin=sdl_mod.BasinName("Kentucky"),
            status=sdl_mod.PoolStatusFilter("above_guide"),
        )

    assert [r["lake_id"] for r in result["lakes"]] == ["Buckhorn"]
    assert [u["lake_id"] for u in result["unevaluated_lakes"]] == ["CarrCreek"]
    assert "timed out" in result["unevaluated_lakes"][0]["reason"]
    assert "Carr Creek Lake" in result["filter_note"]


@pytest.mark.asyncio
async def test_no_data_status_filter_has_no_unevaluated_list():
    mock = _mock_by_lake({}, errors={"CarrCreek": "boom"})
    with patch.object(sdl_mod, "get_lake_conditions", new=mock):
        result = await sdl_mod.summarize_district_lakes(
            status=sdl_mod.PoolStatusFilter("no_data")
        )

    assert [r["lake_id"] for r in result["lakes"]] == ["CarrCreek"]
    assert result["unevaluated_lakes"] == []


@pytest.mark.asyncio
async def test_basin_parameter_description_names_values_and_lakes():
    """The agent sees exact basin values with their lakes in the tool schema."""
    mcp = FastMCP("test")
    sdl_mod.register(mcp)
    tool = {t.name: t for t in await mcp.list_tools()}["summarize_district_lakes"]
    schema_text = str(tool.parameters)
    assert "'Kentucky' (Buckhorn Lake, Carr Creek Lake)" in schema_text
    assert "Kentucky River basin" in schema_text


# ── Flood storage ranking (regression: "most flood storage" answered "none") ──


@pytest.mark.asyncio
async def test_flood_storage_ranking_puts_patoka_first():
    """LRL report 2026-10-09: Patoka 21.23%, Carr Creek 7.50%, Cave Run 2.71%."""
    utils = {"Patoka": 21.23, "CarrCreek": 7.50, "CaveRun": 2.71, "Green": -0.90}

    async def _fake(lake):
        lid = lake.value
        return _make_lake_result(
            lid,
            pool_status="above_guide" if utils.get(lid, -0.1) > 0 else "below_guide",
            percent_util=utils.get(lid, -0.1),
        )

    mock = AsyncMock(side_effect=_fake)
    with patch.object(sdl_mod, "get_lake_conditions", new=mock):
        result = await sdl_mod.summarize_district_lakes()

    ranking = result["flood_storage_ranking"]
    assert [r["lake_id"] for r in ranking[:3]] == ["Patoka", "CarrCreek", "CaveRun"]
    assert ranking[0]["percent_util"] == 21.23
    assert len(ranking) == 17
    assert result["counts"]["at_or_above_flood"] == 0


@pytest.mark.asyncio
async def test_flood_storage_ranking_skips_lakes_without_percent_util():
    mock = _mock_by_lake({}, errors={"Patoka": "boom"})
    with patch.object(sdl_mod, "get_lake_conditions", new=mock):
        result = await sdl_mod.summarize_district_lakes()

    ids = [r["lake_id"] for r in result["flood_storage_ranking"]]
    assert "Patoka" not in ids


@pytest.mark.asyncio
async def test_ranking_respects_basin_but_not_status_filter():
    mock = _mock_by_lake({"Barren": "above_guide"})
    with patch.object(sdl_mod, "get_lake_conditions", new=mock):
        result = await sdl_mod.summarize_district_lakes(
            basin=sdl_mod.BasinName("Green River"),
            status=sdl_mod.PoolStatusFilter("above_guide"),
        )

    assert len(result["flood_storage_ranking"]) == 4
    assert [r["lake_id"] for r in result["lakes"]] == ["Barren"]


@pytest.mark.asyncio
async def test_status_description_explains_flood_storage_terms():
    mcp = FastMCP("test")
    sdl_mod.register(mcp)
    tool = {t.name: t for t in await mcp.list_tools()}["summarize_district_lakes"]
    text = str(tool.parameters) + str(tool.description)
    assert "flood_storage_ranking" in text
    assert "TOP of the flood" in text
