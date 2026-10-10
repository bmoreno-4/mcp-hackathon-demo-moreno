"""Tests for the get_lake_trend tool."""

from __future__ import annotations

import datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from lrl_reservoirs.lakes import LakeName
from lrl_reservoirs.tools import lake_trend as lt_mod
from lrl_reservoirs.utils import UpstreamServiceError

UTC = datetime.timezone.utc
GUIDE_FT = 535.7
STOR_AT_GUIDE = 100_000.0
STOR_AT_FLOOD = 200_000.0


def _elev_series(values_newest_first: list[float]) -> list[lt_mod.Point]:
    """6-hourly readings ending now (plus a minute so all fall in the window)."""
    now = datetime.datetime.now(UTC) + datetime.timedelta(minutes=1)
    pts = [
        (now - datetime.timedelta(hours=6 * k), v)
        for k, v in enumerate(values_newest_first)
    ]
    return sorted(pts)


def _stor_series(rise_per_hour: float = 1000.0) -> list[lt_mod.Point]:
    """Hourly storage: flat at 120,000 ac-ft, then rising over the last 24 h."""
    now = datetime.datetime.now(UTC) + datetime.timedelta(minutes=1)
    pts = []
    for h in range(0, 80):
        value = 120_000.0 + max(0, 24 - h) * rise_per_hour
        pts.append((now - datetime.timedelta(hours=h), value))
    return sorted(pts)


def _patches(elev: list[lt_mod.Point], stor: list[lt_mod.Point]):
    async def fake_series(ts_id: str, unit: str, begin: Any, end: Any):
        return elev if unit == "ft" else stor

    async def fake_level(lake_id: str, name: str, at: Any) -> float:
        return STOR_AT_GUIDE if name == "Bottom of Flood Control" else STOR_AT_FLOOD

    return (
        patch.object(lt_mod, "_fetch_series", new=fake_series),
        patch.object(
            lt_mod, "_fetch_guide_curve", new=AsyncMock(return_value=GUIDE_FT)
        ),
        patch.object(lt_mod, "_fetch_storage_level", new=fake_level),
    )


async def _run(elev, stor, days: int = 3) -> dict[str, Any]:
    p1, p2, p3 = _patches(elev, stor)
    with p1, p2, p3:
        return await lt_mod.get_lake_trend(
            lake=LakeName("Patoka"),  # type: ignore[call-arg]
            days=days,
        )


# ── Tool behaviour ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rising_after_rain():
    # Flat at 538.0 until 24 h ago, then +0.3 ft every 6 hours.
    elev = _elev_series([539.2, 538.9, 538.6, 538.3] + [538.0] * 9)
    result = await _run(elev, _stor_series())

    assert result["lake_id"] == "Patoka"
    assert result["change_24hr_ft"] == 1.2
    assert result["direction_24hr"] == "rising"
    assert result["direction_over_window"] == "rising"
    assert result["change"]["elevation_ft"] == 1.2
    assert result["start"]["deviation_from_guide_curve_ft"] == 2.3
    assert result["latest"]["deviation_from_guide_curve_ft"] == 3.5
    assert result["guide_curve_trend"] == "moving_away_from_guide"
    # Percent Util: (120,000-100,000)/100,000 = 20.0 → (144,000-100,000) = 44.0
    assert result["start"]["percent_util"] == 20.0
    assert result["latest"]["percent_util"] == 44.0
    assert result["change"]["percent_util"] == 24.0
    assert result["change"]["storage_acre_ft"] == 24000.0
    assert result["peak"]["elevation_ft"] == 539.2
    assert result["low"]["elevation_ft"] == 538.0
    assert result["observation_count"] == 13
    assert result["readings"][0]["elevation_ft"] == 538.0
    assert result["readings"][-1]["elevation_ft"] == 539.2
    assert "error" not in result


@pytest.mark.asyncio
async def test_falling_toward_guide():
    elev = _elev_series([537.0, 537.3, 537.6, 537.9] + [538.2] * 9)
    result = await _run(elev, _stor_series(rise_per_hour=0.0))
    assert result["change_24hr_ft"] == -1.2
    assert result["direction_24hr"] == "falling"
    assert result["guide_curve_trend"] == "moving_toward_guide"


@pytest.mark.asyncio
async def test_steady_within_threshold():
    elev = _elev_series([538.05, 538.0, 538.0, 538.0, 538.0])
    result = await _run(elev, _stor_series(rise_per_hour=0.0), days=1)
    assert result["change_24hr_ft"] == 0.05
    assert result["direction_24hr"] == "steady"
    assert result["guide_curve_trend"] == "no_change"


@pytest.mark.asyncio
async def test_window_limits_readings_to_requested_days():
    elev = _elev_series([538.0] * 13)
    result = await _run(elev, _stor_series(), days=1)
    # 1 day of 6-hourly readings = 5 readings (now, -6, -12, -18, -24 h).
    assert result["observation_count"] == 5


@pytest.mark.asyncio
async def test_no_24hr_change_when_gap_in_readings():
    now = datetime.datetime.now(UTC)
    elev = [(now - datetime.timedelta(hours=60), 538.0), (now, 538.5)]
    result = await _run(elev, _stor_series())
    assert result["change_24hr_ft"] is None
    assert result["direction_24hr"] == "insufficient_data"
    assert result["change"]["elevation_ft"] == 0.5


@pytest.mark.asyncio
async def test_error_when_too_few_readings():
    elev = _elev_series([538.0])
    result = await _run(elev, _stor_series())
    assert "error" in result
    assert result["observation_count"] == 1
    assert "lkreport" in result["data_note"]


@pytest.mark.asyncio
async def test_missing_storage_gives_null_percent_util():
    elev = _elev_series([538.3, 538.0, 538.0, 538.0, 538.0])
    result = await _run(elev, [], days=1)
    assert result["latest"]["percent_util"] is None
    assert result["change"]["percent_util"] is None
    assert result["change_24hr_ft"] == 0.3


# ── Helpers ───────────────────────────────────────────────────────────────────


def test_to_points_skips_nulls_and_sorts():
    data = {
        "values": [
            [1_760_004_000_000, 538.4, 0],
            [1_760_000_000_000, None, 0],
            [1_759_990_000_000, 538.1, 0],
        ]
    }
    points = lt_mod._to_points(data)
    assert [v for _, v in points] == [538.1, 538.4]
    assert points[0][0].tzinfo is not None


def test_to_points_handles_missing_values():
    assert lt_mod._to_points({}) == []


@pytest.mark.asyncio
async def test_fetch_series_returns_empty_on_upstream_error():
    with patch.object(
        lt_mod,
        "cwms_get",
        new=AsyncMock(side_effect=UpstreamServiceError("CWMS API request failed.")),
    ):
        now = datetime.datetime.now(UTC)
        assert await lt_mod._fetch_series("x", "ft", now, now) == []


@pytest.mark.asyncio
async def test_fetch_series_requests_named_series():
    mock = AsyncMock(return_value={"values": []})
    with patch.object(lt_mod, "cwms_get", new=mock):
        now = datetime.datetime.now(UTC)
        await lt_mod._fetch_series("Patoka.Elev.Inst.0.0.lrldlb-rev", "ft", now, now)
    params = mock.await_args.kwargs["params"]
    assert mock.await_args.args == ("timeseries",)
    assert params["name"] == "Patoka.Elev.Inst.0.0.lrldlb-rev"
    assert params["office"] == "LRL"
    assert params["unit"] == "ft"


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        (0.11, "rising"),
        (-0.11, "falling"),
        (0.1, "steady"),
        (None, "insufficient_data"),
    ],
)
def test_direction(change, expected):
    assert lt_mod._direction(change) == expected


@pytest.mark.asyncio
async def test_tool_registered_with_day_limits():
    from lrl_reservoirs.app import mcp

    tools = {t.name: t for t in await mcp.list_tools()}
    assert "get_lake_trend" in tools
    days = tools["get_lake_trend"].parameters["properties"]["days"]
    assert days["minimum"] == 1
    assert days["maximum"] == 14
