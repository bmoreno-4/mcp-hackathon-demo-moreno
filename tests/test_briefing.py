"""Tests for the district_briefing prompt and the wxO agent instructions."""

from __future__ import annotations

import pathlib

import pytest

from lrl_reservoirs.prompts import briefing

INSTRUCTIONS = (
    pathlib.Path(__file__).parent.parent
    / "deploy"
    / "ibm"
    / "local-mcp-toolkit"
    / "agent_instructions.md"
)


def test_district_prompt_calls_tools_in_order():
    text = briefing.build_briefing_prompt()
    assert "all 17 Louisville District lakes" in text
    order = [
        text.index("check_against_daily_report"),
        text.index("summarize_district_lakes"),
        text.index("get_lake_trend"),
    ]
    assert order == sorted(order)
    assert "lkreport.html" in text


def test_basin_prompt_passes_basin_filter():
    text = briefing.build_briefing_prompt("Green River")
    assert "the Green River basin" in text
    assert "summarize_district_lakes (basin='Green River')" in text


def test_unknown_basin_lists_valid_basins():
    text = briefing.build_briefing_prompt("Ohio")
    assert "not an LRL basin" in text
    assert "Mid. Wabash" in text
    assert "summarize_district_lakes" not in text


def test_blank_basin_means_whole_district():
    assert "all 17" in briefing.build_briefing_prompt("  ")


def test_prompt_keeps_terminology_rules():
    text = briefing.build_briefing_prompt()
    assert "guide curve" in text
    assert "no data" in text
    assert "flood risk" in text


@pytest.mark.asyncio
async def test_prompt_is_registered_and_renders():
    from lrl_reservoirs.app import mcp

    prompts = {p.name for p in await mcp.list_prompts()}
    assert "district_briefing" in prompts

    result = await mcp.render_prompt("district_briefing", {"basin": "Kentucky"})
    text = result.messages[0].content.text
    assert "the Kentucky basin" in text


@pytest.mark.asyncio
async def test_agent_instructions_name_every_registered_tool():
    """wxO defect: instructions named a tool that did not exist. Keep the
    pasted agent instructions in step with the tools the server registers."""
    from lrl_reservoirs.app import mcp

    text = INSTRUCTIONS.read_text(encoding="utf-8")
    for tool in await mcp.list_tools():
        assert tool.name in text, f"agent_instructions.md does not mention {tool.name}"
    assert "briefing" in text.lower()


def test_prompt_and_instructions_default_to_plain_language():
    """2026-10-21: briefings were accurate but written for operators."""
    prompt = briefing.build_briefing_prompt()
    instructions = INSTRUCTIONS.read_text(encoding="utf-8")
    for text in (prompt, instructions):
        assert "Writing for the public" in text
        assert "target level for this time of year" in text
        assert "Eastern labels" in text
        assert "technical detail" in text


def test_prompt_and_instructions_forbid_working_out_weekdays():
    """2026-10-21: the agent called Wednesday 'Friday'."""
    for text in (briefing.build_briefing_prompt(), INSTRUCTIONS.read_text()):
        assert "report_date_label" in text
        assert "Never work out a day of the week" in text


def test_public_wording_links_lake_page_and_explains_its_diagram():
    for text in (briefing.build_briefing_prompt(), INSTRUCTIONS.read_text()):
        assert "lake_page_url" in text
        assert "Top of Conservation" in text
        assert "inflow and outflow" in text


def test_public_version_has_its_own_sections_and_per_lake_values():
    """2026-10-21: a public briefing mixed in the technical sections, gave a
    made-up 1%-8% range and put Barren's change under Patoka."""
    for text in (briefing.build_briefing_prompt(), INSTRUCTIONS.read_text()):
        assert "using only these sections" in text
        assert "never summarize several lakes as a range" in text
        assert "only if it\nis change_24hr_ft" in text
        assert "exactly as given in\n  changes_since_report" in text
