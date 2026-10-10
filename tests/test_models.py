"""Every tool's output must match its Pydantic model (and so its published
output schema). Runs each tool on realistic mocked data, including error paths,
and validates the result with extra fields forbidden."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from lrl_reservoirs import models
from lrl_reservoirs.lakes import LAKES, LakeName
from lrl_reservoirs.tools import check_report as cr_mod
from lrl_reservoirs.tools import lake_conditions as lc_mod
from lrl_reservoirs.tools import list_lakes as ll_mod
from lrl_reservoirs.tools import summarize_district_lakes as sdl_mod
from lrl_reservoirs.utils import UpstreamServiceError
from tests.test_check_report import REPORT_HTML, _live_from_report
from tests.test_lake_conditions import (
    _fake_guide,
    _fake_stor,
    _fake_stor_level,
    _fake_ts,
)
from tests.test_lake_trend import _elev_series, _run, _stor_series

TOOL_MODELS = {
    "get_lake_conditions": models.LakeConditions,
    "summarize_district_lakes": models.DistrictSummary,
    "list_lakes": models.LakeDirectory,
    "get_lake_trend": models.LakeTrend,
    "check_against_daily_report": models.ReportCheck,
}


# ── Published schemas ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_every_tool_publishes_its_model_as_output_schema():
    from lrl_reservoirs.app import mcp

    tools = {t.name: t for t in await mcp.list_tools()}
    assert set(tools) == set(TOOL_MODELS)
    for name, model in TOOL_MODELS.items():
        schema = tools[name].output_schema
        assert schema is not None, name
        assert set(schema["properties"]) == set(model.model_fields), name


def test_schemas_are_objects_that_forbid_unknown_fields():
    for model in TOOL_MODELS.values():
        schema = model.model_json_schema()
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False


def test_pool_status_values_are_listed_in_schema():
    schema = models.LakeConditions.model_json_schema()
    allowed = schema["properties"]["pool_status"]["enum"]
    assert "above_guide" in allowed
    assert "no_guide/normal" in allowed


# ── get_lake_conditions ───────────────────────────────────────────────────────


async def _conditions(side_effect: list[Any]) -> dict[str, Any]:
    with patch.object(lc_mod, "cwms_get", new=AsyncMock(side_effect=side_effect)):
        return await lc_mod.get_lake_conditions(
            lake=LakeName("Barren")  # type: ignore[call-arg]
        )


@pytest.mark.asyncio
async def test_lake_conditions_success_matches_model():
    result = await _conditions(
        [
            _fake_ts(elev_ft=551.0),
            _fake_guide(guide_ft=550.4),
            _fake_stor(storage_af=350000.0),
            _fake_stor_level(345000.0),
            _fake_stor_level(873000.0),
        ]
    )
    models.LakeConditions.model_validate(result)


@pytest.mark.asyncio
async def test_lake_conditions_without_guide_curve_matches_model():
    result = await _conditions(
        [
            _fake_ts(elev_ft=551.0),
            UpstreamServiceError("CWMS API request failed."),
            _fake_stor(storage_af=350000.0),
            _fake_stor_level(345000.0),
            _fake_stor_level(873000.0),
        ]
    )
    assert result["pool_status"].startswith("no_guide/")
    models.LakeConditions.model_validate(result)


@pytest.mark.asyncio
async def test_lake_conditions_error_matches_model():
    result = await _conditions([UpstreamServiceError("CWMS API request failed.")])
    parsed = models.LakeConditions.model_validate(result)
    assert parsed.error == "CWMS API request failed."
    assert parsed.pool_status == "no_data"


@pytest.mark.asyncio
async def test_lake_conditions_no_values_matches_model():
    result = await _conditions([{"values": []}])
    models.LakeConditions.model_validate(result)


# ── summarize_district_lakes ──────────────────────────────────────────────────


def _full_lake(lake_id: str, status: str, dev: float, pct: float) -> dict:
    meta = LAKES[lake_id]
    return {
        "lake_id": lake_id,
        "public_name": meta["public_name"],
        "basin": meta["basin"],
        "elevation_ft": 500.0 + dev,
        "vertical_datum": "NGVD-29",
        "as_of": "2026-10-10T10:00:00Z",
        "observation_age_hours": 2.0,
        "stale": False,
        "guide_curve_ft": 500.0,
        "deviation_from_guide_curve_ft": dev,
        "pool_status": status,
        "percent_to_flood_pool": 1.0,
        "storage_acre_ft": 100000.0,
        "storage_at_guide_curve_acre_ft": 99000.0,
        "storage_at_flood_pool_acre_ft": 200000.0,
        "percent_util": pct,
        "reference_levels": {
            "winter_pool_ft": meta["top_of_normal_ft"],
            "summer_pool_ft": meta["top_of_conservation_ft"],
            "flood_pool_ft": meta["top_of_flood_ft"],
        },
        "data_note": "note",
    }


def _district() -> list[dict]:
    lakes = []
    for i, lid in enumerate(LAKES):
        if i == 0:
            lake = _full_lake(lid, "no_data", 0.0, 0.0)
            lake.update(
                elevation_ft=None,
                deviation_from_guide_curve_ft=None,
                percent_util=None,
                error="CWMS API request failed.",
            )
        elif i % 3 == 0:
            lake = _full_lake(lid, "below_guide", -0.5, -0.8)
        else:
            lake = _full_lake(lid, "above_guide", 1.0, 2.0)
        lakes.append(lake)
    return lakes


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"basin": sdl_mod.BasinName("Green River")},
        {"status": sdl_mod.PoolStatusFilter("above_guide")},
    ],
)
async def test_district_summary_matches_model(kwargs):
    with patch.object(
        sdl_mod, "get_lake_conditions", new=AsyncMock(side_effect=_district())
    ):
        result = await sdl_mod.summarize_district_lakes(**kwargs)
    parsed = models.DistrictSummary.model_validate(result)
    if "status" in kwargs:
        assert parsed.unevaluated_lakes  # the no-data lake is reported, not hidden


# ── list_lakes ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_lakes_matches_model():
    models.LakeDirectory.model_validate(await ll_mod.list_lakes())


# ── get_lake_trend ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_lake_trend_matches_model():
    elev = _elev_series([539.2, 538.9, 538.6, 538.3] + [538.0] * 9)
    result = await _run(elev, _stor_series())
    models.LakeTrend.model_validate(result)


@pytest.mark.asyncio
async def test_lake_trend_without_storage_matches_model():
    elev = _elev_series([538.3, 538.0, 538.0, 538.0, 538.0])
    models.LakeTrend.model_validate(await _run(elev, [], days=1))


@pytest.mark.asyncio
async def test_lake_trend_error_matches_model():
    result = await _run(_elev_series([538.0]), _stor_series())
    assert models.LakeTrend.model_validate(result).error


# ── check_against_daily_report ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_report_check_matches_model():
    def live(lake: Any) -> dict[str, Any]:
        if lake.value == "Patoka":
            return {"elevation_ft": None, "error": "CWMS API request failed."}
        if lake.value == "Nolin":
            changed = _live_from_report(lake)
            changed.update(elevation_ft=516.0, as_of="2026-10-09T16:00:00Z")
            return changed
        return _live_from_report(lake)

    with (
        patch.object(
            cr_mod, "fetch_lake_report_html", new=AsyncMock(return_value=REPORT_HTML)
        ),
        patch.object(cr_mod, "get_lake_conditions", new=AsyncMock(side_effect=live)),
    ):
        result = await cr_mod.check_against_daily_report()

    parsed = models.ReportCheck.model_validate(result)
    assert parsed.counts.no_live_data == 1
    assert parsed.counts.changed_since_report == 1


@pytest.mark.asyncio
async def test_report_check_error_matches_model():
    with patch.object(
        cr_mod,
        "fetch_lake_report_html",
        new=AsyncMock(side_effect=UpstreamServiceError("LRL lake report failed.")),
    ):
        result = await cr_mod.check_against_daily_report()
    assert models.ReportCheck.model_validate(result).error


def test_unknown_field_is_rejected():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        models.LakeDirectory.model_validate(
            {"lakes": [], "vertical_datum": "NGVD-29", "source": "x", "extra": 1}
        )
