"""Pydantic models for what each tool returns.

Inputs are validated from the tool signatures (fixed lake, basin and status
lists, and a 1-14 day range). These models describe the outputs: each tool is
registered with ``output_schema=<Model>.model_json_schema()``, so MCP clients
see exactly which fields come back, their types and their allowed values before
they call the tool. ``tests/test_models.py`` runs every tool and checks its
result against its model, so the published schema and the code stay in step.

Models forbid extra fields so the schema is complete; a field that is only
sometimes present (``error``, ``reason``, …) has a default of None.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

PoolStatus = Literal[
    "below_guide",
    "at_guide",
    "above_guide",
    "at_or_above_flood",
    "unknown",
    "no_data",
    "no_guide/below_normal",
    "no_guide/normal",
    "no_guide/above_conservation",
    "no_guide/at_or_above_flood",
    "no_guide/unknown",
]

Direction = Literal["rising", "falling", "steady", "insufficient_data"]
GuideTrend = Literal[
    "moving_toward_guide", "moving_away_from_guide", "no_change", "unknown"
]
ReportStatus = Literal[
    "matches", "changed_since_report", "differs", "no_live_data", "not_in_report"
]

FT = "feet, NGVD-29"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


def output_schema(model: type[BaseModel]) -> dict[str, Any]:
    """JSON schema for a tool's ``output_schema`` argument."""
    return model.model_json_schema()


# ── Shared ────────────────────────────────────────────────────────────────────


class ReferenceLevels(_Model):
    winter_pool_ft: float | None = Field(description=f"Winter pool, {FT}")
    summer_pool_ft: float | None = Field(description=f"Summer pool, {FT}")
    flood_pool_ft: float | None = Field(description=f"Top of flood pool, {FT}")


# ── get_lake_conditions ───────────────────────────────────────────────────────


class LakeConditions(_Model):
    """Current conditions for one lake (get_lake_conditions)."""

    lake_id: str = Field(description="CWMS location ID, e.g. 'Patoka'")
    public_name: str
    basin: str
    elevation_ft: float | None = Field(description=f"Latest pool elevation, {FT}")
    vertical_datum: str | None
    as_of: str | None = Field(description="UTC time of the elevation reading")
    observation_age_hours: float | None
    stale: bool = Field(description="True when the reading is over 30 hours old")
    guide_curve_ft: float | None = Field(
        description=f"Today's seasonal guide curve (the report's 'Pool'), {FT}"
    )
    deviation_from_guide_curve_ft: float | None = Field(
        description="Elevation minus guide curve; the report's 'Dev. from Pool'"
    )
    pool_status: PoolStatus = Field(
        description="Status vs. the guide curve; no_guide/* when it is unavailable"
    )
    percent_to_flood_pool: float | None = Field(
        description="Elevation-based: 0% at guide curve, 100% at flood pool"
    )
    storage_acre_ft: float | None
    storage_at_guide_curve_acre_ft: float | None
    storage_at_flood_pool_acre_ft: float | None
    percent_util: float | None = Field(
        description="Share of flood storage in use (the report's 'Percent Util')"
    )
    reference_levels: ReferenceLevels
    data_note: str
    error: str | None = Field(
        default=None, description="Present only when elevation could not be read"
    )


# ── summarize_district_lakes ──────────────────────────────────────────────────


class StatusCounts(_Model):
    above_guide: int
    at_guide: int
    below_guide: int
    at_or_above_flood: int
    no_data: int


class LakesByStatus(_Model):
    """Public names per status; each lake in scope appears exactly once."""

    above_guide: list[str]
    at_guide: list[str]
    below_guide: list[str]
    at_or_above_flood: list[str]
    no_data: list[str]


class SummaryFilters(_Model):
    basin: str | None
    status: str | None


class UnevaluatedLake(_Model):
    lake_id: str | None
    public_name: str | None
    pool_status: str | None
    reason: str


class FloodStorageRank(_Model):
    lake_id: str | None
    public_name: str | None
    percent_util: float | None
    deviation_from_guide_curve_ft: float | None
    as_of: str | None


class DistrictSummary(_Model):
    """District or basin snapshot (summarize_district_lakes)."""

    as_of_utc: str
    total_lakes: int
    counts: StatusCounts = Field(description="Whole district, before filters")
    data_note: str
    filters: SummaryFilters
    lakes_in_scope: int
    matched_lakes: int
    unevaluated_lakes: list[UnevaluatedLake] = Field(
        description="Lakes with no usable data; report them as unknown, not 'none'"
    )
    filter_note: str | None
    flood_storage_ranking: list[FloodStorageRank] = Field(
        description="Lakes in scope by percent_util, highest first"
    )
    lakes_by_status: LakesByStatus
    lakes: list[LakeConditions]


# ── list_lakes ────────────────────────────────────────────────────────────────


class LakeReference(_Model):
    lake_id: str
    public_name: str
    basin: str
    winter_pool_ft: float | None
    summer_pool_ft: float | None
    flood_pool_ft: float | None


class LakeDirectory(_Model):
    """Static lake directory and pool schedule (list_lakes)."""

    lakes: list[LakeReference]
    vertical_datum: str
    source: str


# ── get_lake_trend ────────────────────────────────────────────────────────────


class TrendSnapshot(_Model):
    time: str = Field(description="UTC time of the elevation reading")
    elevation_ft: float
    guide_curve_ft: float | None
    deviation_from_guide_curve_ft: float | None
    storage_acre_ft: float | None
    percent_util: float | None


class TrendChange(_Model):
    """Latest minus start."""

    elevation_ft: float
    deviation_from_guide_curve_ft: float | None
    percent_util: float | None
    storage_acre_ft: float | None


class Reading(_Model):
    time: str
    elevation_ft: float


class LakeTrend(_Model):
    """Change over 1-14 days for one lake (get_lake_trend)."""

    lake_id: str
    public_name: str
    basin: str
    days: int
    start: TrendSnapshot | None = None
    latest: TrendSnapshot | None = None
    change: TrendChange | None = None
    change_24hr_ft: float | None = None
    direction_24hr: Direction | None = None
    direction_over_window: Direction | None = None
    guide_curve_trend: GuideTrend | None = None
    peak: Reading | None = None
    low: Reading | None = None
    observation_count: int
    readings: list[Reading] | None = None
    data_note: str
    error: str | None = Field(
        default=None, description="Present only when there were too few readings"
    )


# ── check_against_daily_report ────────────────────────────────────────────────


class ReportValues(_Model):
    pool_ft: float | None
    dev_from_guide_ft: float | None
    guide_curve_ft: float | None
    percent_util: float | None
    change_24hr_ft: float | None
    precip_24hr_in: float | None
    inflow_24hr_avg_cfs: float | None
    outflow_6am_cfs: float | None


class LiveValues(_Model):
    pool_ft: float | None
    dev_from_guide_ft: float | None
    guide_curve_ft: float | None
    percent_util: float | None
    pool_status: PoolStatus | None


class LiveMinusReport(_Model):
    pool_ft: float | None
    guide_curve_ft: float | None
    percent_util: float | None


class ReportLakeCheck(_Model):
    lake_id: str
    public_name: str
    basin: str
    live_as_of: str | None
    status: ReportStatus
    report: ReportValues | None = None
    reason: str | None = None
    live: LiveValues | None = None
    live_minus_report: LiveMinusReport | None = None
    outside_tolerance: list[str] | None = None


class ReportTolerances(_Model):
    pool_ft: float
    guide_curve_ft: float
    percent_util: float


class ReportCounts(_Model):
    matches: int
    changed_since_report: int
    differs: int
    no_live_data: int
    not_in_report: int


class ReportCheck(_Model):
    """Live values vs. today's LRL Daily Lake Report (check_against_daily_report)."""

    report_url: str
    report_date: str | None = None
    report_reference_time_utc: str | None = None
    report_is_today: bool | None = None
    tolerances: ReportTolerances | None = None
    counts: ReportCounts | None = None
    lakes: list[ReportLakeCheck] | None = None
    error: str | None = Field(
        default=None, description="Present only when the report could not be read"
    )
