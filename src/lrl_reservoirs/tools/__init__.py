"""Tools submodule — one tool per file.

Each tool module exposes a `register(mcp)` function that attaches its
`@mcp.tool`-decorated function to the server. This file aggregates them
into a single `register_tools(mcp)` that `app.py` calls.

To add a tool:
  1. Create `tools/<your_tool>.py` with a `register(mcp)` function.
  2. Import it below and call `<your_tool>.register(mcp)` inside register_tools.
"""

from __future__ import annotations

from lrl_reservoirs.tools import lake_conditions, summarize_district_lakes


def register_tools(mcp) -> None:
    """Register all tools with the MCP server."""
    lake_conditions.register(mcp)
    summarize_district_lakes.register(mcp)


__all__ = ["register_tools"]
