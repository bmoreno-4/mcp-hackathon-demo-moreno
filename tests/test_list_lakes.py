"""Tests for the list_lakes reference tool."""

from __future__ import annotations

import pytest
from fastmcp import FastMCP

from lrl_reservoirs.tools import list_lakes as ll_mod
from lrl_reservoirs.tools.summarize_district_lakes import BasinName


@pytest.mark.asyncio
async def test_list_lakes_returns_all_17():
    result = await ll_mod.list_lakes()
    assert len(result["lakes"]) == 17


@pytest.mark.asyncio
async def test_brookville_pools_match_lake_report():
    """LRL Daily Lake Report: Brookville winter 740.0, summer 748.0, flood 775.0."""
    result = await ll_mod.list_lakes()
    brook = next(r for r in result["lakes"] if r["lake_id"] == "Brookville")
    assert brook["winter_pool_ft"] == 740.0
    assert brook["summer_pool_ft"] == 748.0
    assert brook["flood_pool_ft"] == 775.0
    assert brook["basin"] == "Whitewater"


@pytest.mark.asyncio
async def test_list_lakes_basin_filter():
    result = await ll_mod.list_lakes(basin=BasinName("Kentucky"))
    assert {r["lake_id"] for r in result["lakes"]} == {"Buckhorn", "CarrCreek"}


@pytest.mark.asyncio
async def test_list_lakes_is_registered_with_reference_description():
    from lrl_reservoirs.app import mcp

    tools = {t.name: t for t in await mcp.list_tools()}
    assert "list_lakes" in tools
    description = " ".join(tools["list_lakes"].description.split())
    assert "summer and winter pools" in description


@pytest.mark.asyncio
async def test_register_on_fresh_server():
    mcp = FastMCP("test")
    ll_mod.register(mcp)
    assert "list_lakes" in {t.name for t in await mcp.list_tools()}
