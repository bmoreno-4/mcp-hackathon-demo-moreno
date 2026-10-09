"""Smoke tests for the LRL Reservoir Conditions MCP server.

These verify the server imports and every submodule registers without error.
"""

from __future__ import annotations

import pytest
from fastmcp import FastMCP

from lrl_reservoirs.app import mcp
from lrl_reservoirs.config import settings


def test_server_is_importable():
    assert mcp is not None


def test_server_has_name():
    assert mcp.name == "LRL Reservoir Conditions"


def test_default_transport_is_stdio():
    """Guard against accidentally shipping with a non-stdio default."""
    assert settings.mcp_transport == "stdio"


def test_tools_register_without_error():
    from lrl_reservoirs.tools import register_tools

    register_tools(FastMCP("test"))  # must not raise


def test_prompts_register_without_error():
    from lrl_reservoirs.prompts import register_prompts

    register_prompts(FastMCP("test"))  # must not raise


def test_resources_register_without_error():
    from lrl_reservoirs.resources import register_resources

    register_resources(FastMCP("test"))  # must not raise


def test_routes_register_without_error():
    from lrl_reservoirs.routes import register_routes

    register_routes(FastMCP("test"))  # must not raise


@pytest.mark.asyncio
async def test_tools_are_discoverable():
    """Both LRL tools should be registered and visible via the MCP API."""
    tools = await mcp.list_tools()
    tool_names = {t.name for t in tools}
    assert "get_lake_conditions" in tool_names
    assert "summarize_district_lakes" in tool_names
