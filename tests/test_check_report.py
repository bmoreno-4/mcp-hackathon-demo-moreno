"""Tests for the check_against_daily_report tool and the lake report parser."""

from __future__ import annotations

import datetime
import pathlib
import ssl
import subprocess
from typing import Any
from unittest.mock import AsyncMock, patch

import certifi
import httpx
import pytest

from lrl_reservoirs import utils
from lrl_reservoirs.lake_report import (
    parse_report_date,
    parse_report_rows,
    report_reference_time,
)
from lrl_reservoirs.lakes import LAKES, LakeName
from lrl_reservoirs.tools import check_report as cr_mod

REPORT_HTML = (
    pathlib.Path(__file__).parent.parent
    / "eval"
    / "reports"
    / "lrl_lake_report_2026-10-09.html"
).read_text(encoding="utf-8")

REF_TIME = datetime.datetime(2026, 10, 9, 10, 0, tzinfo=datetime.timezone.utc)


# ── Parser ────────────────────────────────────────────────────────────────────


def test_parse_report_date_and_reference_time():
    report_date = parse_report_date(REPORT_HTML)
    assert report_date == datetime.date(2026, 10, 9)
    # 06:00 EDT == 10:00 UTC
    assert report_reference_time(report_date) == REF_TIME


def test_reference_time_uses_standard_time_in_winter():
    ref = report_reference_time(datetime.date(2026, 12, 15))
    assert ref.hour == 11  # 06:00 EST == 11:00 UTC


def test_parse_report_rows_values():
    rows = parse_report_rows(REPORT_HTML)
    assert set(rows) == set(LAKES)
    patoka = rows["Patoka"]
    assert patoka["basin"] == "Mid. Wabash"
    assert patoka["todays_pool_ft"] == 538.6
    assert patoka["dev_from_pool_ft"] == 2.9
    assert patoka["percent_util"] == 21.23
    assert patoka["outflow_6am_cfs"] == 296
    # Blank basin cell inherits the basin above.
    assert rows["WHHarsha"]["basin"] == "Little Miami"
    assert rows["Barren"]["precip_24hr_in"] == 0.0


def test_parse_report_rows_rejects_wrong_row_count():
    truncated = REPORT_HTML.split("Patoka")[0] + "</table>"
    with pytest.raises(ValueError, match="Expected 17"):
        parse_report_rows(truncated)


# ── Comparison logic ──────────────────────────────────────────────────────────


def _report_row(**overrides: Any) -> dict[str, Any]:
    row = {
        "basin": "Mid. Wabash",
        "todays_pool_ft": 538.6,
        "dev_from_pool_ft": 2.9,
        "change_24hr_ft": 0.0,
        "precip_24hr_in": 0.0,
        "inflow_24hr_cfs": 0.0,
        "outflow_6am_cfs": 296.0,
        "percent_util": 21.23,
    }
    row.update(overrides)
    return row


def _live(
    elevation: float | None = 538.6,
    guide: float = 535.7,
    percent_util: float | None = 21.2,
    as_of: str = "2026-10-09T10:00:00Z",
) -> dict[str, Any]:
    result = {
        "elevation_ft": elevation,
        "guide_curve_ft": guide,
        "deviation_from_guide_curve_ft": (
            round(elevation - guide, 2) if elevation is not None else None
        ),
        "percent_util": percent_util,
        "pool_status": "above_guide",
        "as_of": as_of,
    }
    if elevation is None:
        result["error"] = "CWMS API request failed."
    return result


def test_compare_matches_within_tolerance():
    entry = cr_mod.compare_lake("Patoka", _report_row(), _live(), REF_TIME)
    assert entry["status"] == "matches"
    assert entry["outside_tolerance"] == []
    assert entry["report"]["guide_curve_ft"] == 535.7
    assert entry["live_minus_report"]["percent_util"] == -0.03


def test_compare_changed_since_report_when_live_is_newer():
    live = _live(elevation=539.4, percent_util=24.9, as_of="2026-10-09T16:00:00Z")
    entry = cr_mod.compare_lake("Patoka", _report_row(), live, REF_TIME)
    assert entry["status"] == "changed_since_report"
    assert set(entry["outside_tolerance"]) == {"pool_ft", "percent_util"}
    assert entry["live_minus_report"]["pool_ft"] == 0.8


def test_compare_differs_when_live_is_not_newer():
    live = _live(percent_util=22.0, as_of="2026-10-09T10:00:00Z")
    entry = cr_mod.compare_lake("Patoka", _report_row(), live, REF_TIME)
    assert entry["status"] == "differs"
    assert entry["outside_tolerance"] == ["percent_util"]


def test_compare_no_live_data():
    entry = cr_mod.compare_lake("Patoka", _report_row(), _live(None), REF_TIME)
    assert entry["status"] == "no_live_data"
    assert entry["report"]["pool_ft"] == 538.6
    assert "reason" in entry


def test_compare_not_in_report():
    entry = cr_mod.compare_lake("Patoka", None, _live(), REF_TIME)
    assert entry["status"] == "not_in_report"


# ── Tool ──────────────────────────────────────────────────────────────────────


def _live_from_report(lake: Any) -> dict[str, Any]:
    """Live result that exactly reproduces the 2026-10-09 report row."""
    row = parse_report_rows(REPORT_HTML)[lake.value]
    guide = round(row["todays_pool_ft"] - row["dev_from_pool_ft"], 2)
    return _live(row["todays_pool_ft"], guide, row["percent_util"])


@pytest.mark.asyncio
async def test_tool_all_lakes_match_report():
    with (
        patch.object(
            cr_mod, "fetch_lake_report_html", new=AsyncMock(return_value=REPORT_HTML)
        ),
        patch.object(
            cr_mod,
            "get_lake_conditions",
            new=AsyncMock(side_effect=lambda lake: _live_from_report(lake)),
        ),
    ):
        result = await cr_mod.check_against_daily_report()

    assert result["report_date"] == "2026-10-09"
    assert result["report_reference_time_utc"] == "2026-10-09T10:00:00Z"
    assert result["counts"]["matches"] == 17
    assert len(result["lakes"]) == 17
    assert result["report_url"] == utils.LAKE_REPORT_URL


@pytest.mark.asyncio
async def test_tool_single_lake_only_queries_that_lake():
    mock_live = AsyncMock(side_effect=lambda lake: _live_from_report(lake))
    with (
        patch.object(
            cr_mod, "fetch_lake_report_html", new=AsyncMock(return_value=REPORT_HTML)
        ),
        patch.object(cr_mod, "get_lake_conditions", new=mock_live),
    ):
        result = await cr_mod.check_against_daily_report(
            lake=LakeName("Patoka")  # type: ignore[call-arg]
        )

    assert mock_live.await_count == 1
    assert [e["lake_id"] for e in result["lakes"]] == ["Patoka"]
    assert result["lakes"][0]["report"]["outflow_6am_cfs"] == 296


@pytest.mark.asyncio
async def test_tool_returns_sanitized_error_when_report_unavailable():
    with patch.object(
        cr_mod,
        "fetch_lake_report_html",
        new=AsyncMock(
            side_effect=utils.UpstreamServiceError("LRL lake report request failed.")
        ),
    ):
        result = await cr_mod.check_against_daily_report()
    assert result["error"] == "LRL lake report request failed."
    assert "lakes" not in result


@pytest.mark.asyncio
async def test_tool_reports_format_change():
    with patch.object(
        cr_mod,
        "fetch_lake_report_html",
        new=AsyncMock(return_value="<html><table></table></html>"),
    ):
        result = await cr_mod.check_against_daily_report()
    assert "format may have changed" in result["error"]


@pytest.mark.asyncio
async def test_tool_is_registered():
    from lrl_reservoirs.app import mcp

    tools = {t.name for t in await mcp.list_tools()}
    assert "check_against_daily_report" in tools


# ── Outbound request boundary for the report client ──────────────────────────


class _FakeClient:
    init_kwargs: dict[str, Any] = {}
    requested_url: str | None = None
    response = httpx.Response(200, text="<html>ok</html>")

    def __init__(self, **kwargs: Any) -> None:
        type(self).init_kwargs = kwargs

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    class _Stream:
        def __init__(self, response: httpx.Response) -> None:
            self.response = response

        async def __aenter__(self) -> httpx.Response:
            return self.response

        async def __aexit__(self, *args: object) -> None:
            return None

    def stream(self, method: str, url: str, **kwargs: Any) -> _Stream:
        type(self).requested_url = url
        self.response.request = httpx.Request(method, url)
        return self._Stream(self.response)


@pytest.mark.asyncio
async def test_report_client_uses_fixed_url_and_no_redirects(monkeypatch):
    _FakeClient.response = httpx.Response(200, text="<html>ok</html>")
    monkeypatch.setattr(utils.httpx, "AsyncClient", _FakeClient)

    assert await utils.fetch_lake_report_html() == "<html>ok</html>"
    assert _FakeClient.requested_url == utils.LAKE_REPORT_URL
    assert _FakeClient.init_kwargs["follow_redirects"] is False
    assert _FakeClient.init_kwargs["trust_env"] is False
    ctx = _FakeClient.init_kwargs["verify"]
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.verify_mode == ssl.CERT_REQUIRED  # TLS verification stays on
    assert ctx.check_hostname is True


@pytest.mark.asyncio
async def test_report_client_error_is_sanitized(monkeypatch):
    secret = "internal stack trace token=abc"
    _FakeClient.response = httpx.Response(500, text=secret)
    monkeypatch.setattr(utils.httpx, "AsyncClient", _FakeClient)

    with pytest.raises(utils.UpstreamServiceError) as captured:
        await utils.fetch_lake_report_html()
    assert str(captured.value) == "LRL lake report returned status 500."
    assert secret not in str(captured.value)


@pytest.mark.asyncio
async def test_report_client_enforces_size_cap(monkeypatch):
    _FakeClient.response = httpx.Response(
        200, content=b"x" * (utils.MAX_RESPONSE_BYTES + 1)
    )
    monkeypatch.setattr(utils.httpx, "AsyncClient", _FakeClient)

    with pytest.raises(utils.UpstreamServiceError, match="size limit"):
        await utils.fetch_lake_report_html()


def test_bundled_intermediate_is_the_digicert_ca_and_chains_to_certifi_root():
    """The shipped PEM must be the DigiCert intermediate named in the report
    server's certificate, and must verify against the public root in certifi."""
    pem = utils.REPORT_CA_FILE
    assert pem.exists(), "run the download steps in SECURITY.md"
    text = pem.read_text()
    assert text.count("BEGIN CERTIFICATE") == 1
    assert "PRIVATE KEY" not in text

    subject = subprocess.run(
        ["openssl", "x509", "-in", str(pem), "-noout", "-subject", "-issuer"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "DigiCert Global G2 TLS RSA SHA256 2020 CA1" in subject
    assert "DigiCert Global Root G2" in subject

    verify = subprocess.run(
        ["openssl", "verify", "-CAfile", certifi.where(), str(pem)],
        capture_output=True,
        text=True,
    )
    assert verify.returncode == 0, verify.stdout + verify.stderr
