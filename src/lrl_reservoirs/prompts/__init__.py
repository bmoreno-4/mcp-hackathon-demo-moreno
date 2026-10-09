"""Prompts submodule — reusable conversation starters.

Prompts appear in the client UI as slash commands / preset starters. Each
prompt module exposes a `register(mcp)` function; this file aggregates them
into `register_prompts(mcp)`.

To add a prompt:
  1. Create `prompts/<group>.py` with a `register(mcp)` function.
  2. Import it below and call `<group>.register(mcp)` inside register_prompts.
"""

from __future__ import annotations


def register_prompts(mcp) -> None:
    """Register all prompts with the MCP server."""
    # No prompts registered yet — add imports and calls here.


__all__ = ["register_prompts"]
