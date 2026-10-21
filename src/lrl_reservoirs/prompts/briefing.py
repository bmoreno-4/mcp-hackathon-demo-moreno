"""Prompt: district_briefing

A reusable daily lake briefing for the Louisville District. The prompt tells
the assistant which tools to call, in what order, and how to write the result,
so every briefing has the same sections and the same factual rules.

watsonx Orchestrate does not show MCP prompts to its agents, so the same
workflow is in deploy/ibm/local-mcp-toolkit/agent_instructions.md for pasting
into the agent's instructions.
"""

from __future__ import annotations

from fastmcp import FastMCP

from lrl_reservoirs.lakes import LAKES
from lrl_reservoirs.utils import LAKE_REPORT_URL

BASINS = sorted({meta["basin"] for meta in LAKES.values()})
TREND_LAKE_LIMIT = 3


def build_briefing_prompt(basin: str | None = None) -> str:
    """Return the briefing instructions, optionally limited to one basin."""
    basin = (basin or "").strip() or None
    if basin is not None and basin not in BASINS:
        return (
            f"'{basin}' is not an LRL basin. Ask the user to choose one of: "
            f"{', '.join(BASINS)}, or run the briefing for the whole district."
        )

    scope = f"the {basin} basin" if basin else "all 17 Louisville District lakes"
    basin_arg = f"basin='{basin}'" if basin else "no basin filter"
    basin_filter = (
        f"\n   Keep only lakes in the {basin} basin from its results." if basin else ""
    )

    return f"""Write a daily lake briefing for {scope}.

Call the tools in this order:
1. check_against_daily_report (no lake argument).{basin_filter}
   Note the report date. For rain, use rain_last_24hr exactly as given (empty
   means no rain). For changes, use changes_since_report.
2. summarize_district_lakes ({basin_arg}).
   Use counts, lakes_by_status, flood_storage_ranking and unevaluated_lakes.
3. get_lake_trend (days=3) for up to {TREND_LAKE_LIMIT} lakes: the lakes with the
   highest percent_util above 0, then any lake with a 24-hour change of 0.5 ft or
   more. Skip this step if no lake qualifies.

Write the briefing with these sections (for a technical audience; see
"Writing for the public" below for the default wording):
- Bottom line: report date and the time of the latest live readings; how many
  lakes are above, at and below their guide curve, named from lakes_by_status
  exactly as given (each lake once); any lake with no data.
- Flood storage in use: each lake with percent_util above 0, highest first, with
  its pool elevation and deviation from guide curve.
- Rain and changes since the 6 AM report: lakes with precipitation in the last
  24 hours, and lakes whose status is changed_since_report, with the change.
- Trends: for each lake from step 3, rising, falling or steady over 24 hours,
  the change over 3 days, and whether it is moving toward its guide curve.
- Data notes: lakes with no live data or status differs, stale readings.
- Source: {LAKE_REPORT_URL}

Writing for the public. Unless the user asks for technical or operations
detail, write for a member of the public:
- Start with 2-3 plain sentences: how many lakes are holding extra water, which
  lake is fullest and how full, and whether it rained.
- Say "target level for this time of year" instead of guide curve. Say "above
  target" / "at target" / "below target". Say "flood storage space in use"
  instead of Percent Util, rounded to whole percent ("less than 1%" when under 1).
- Give how far above or below target each lake is in feet, rounded to 0.1 ft,
  instead of listing elevation and guide curve separately.
- Give dates and times from the tools' Eastern labels ("Wednesday, October
  21", "6:00 AM EDT"), not UTC.
- Describe trends in words: "slowly falling, getting closer to its target",
  "steady", "rose about 2 inches".
- Do not mention CWMS, tool names, field names, or negative percentages.
- End with a short "What these terms mean" note and the link to the official
  LRL Daily Lake Report.
- Keep the same facts and rules: no flood risk judgments, no estimates.
If the user asks for technical detail, use the full briefing format with
elevations, guide curve values and percent utilization.

Rules:
- Dates and times: use the Eastern labels the tools return
  (report_date_label, report_time_eastern, as_of_eastern, time_eastern) exactly
  as given. Never work out a day of the week, a date or a time zone yourself.
- Use the guide curve (deviation_from_guide_curve_ft) to say whether a pool is
  high or low. Summer and winter pool are reference values only.
- Any lake above its guide curve is using flood storage; say so with its percent.
- Never report a lake with no data as "none" or "normal"; list it as no data.
- Do not compute your own totals or percentages of the district; use counts.
  Percent Util is per lake: never add, average or total it across lakes.
- Give times for readings. Report numbers factually and make no flood risk
  judgments; the LRL Daily Lake Report is the official source.
"""


def register(mcp: FastMCP) -> None:
    """Register the district_briefing prompt with the MCP server."""
    mcp.prompt(
        name="district_briefing",
        title="LRL Daily Lake Briefing",
        description=(
            "Daily briefing for the Louisville District lakes: report check, "
            "flood storage in use, rain and changes since the 6 AM report, and "
            "3-day trends. Optional basin, one of: " + ", ".join(BASINS) + "."
        ),
    )(district_briefing)


def district_briefing(basin: str | None = None) -> str:
    """Daily LRL lake briefing. Leave basin empty for the whole district."""
    return build_briefing_prompt(basin)
