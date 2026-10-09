"""Tests for the get_lake_conditions tool.

Covers:
  - Pool status derivation logic (all branches, including Patoka's missing
    conservation level)
  - Structured output for a successful API response (mocked)
  - Graceful error dict when the upstream call fails
  - Tool is registered and discoverable on the server
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastmcp import FastMCP

from example_server.tools import lake_conditions as lc_mod
from example_server.tools.lake_conditions import _LAKES, LakeName, _pool_status
from example_server.utils import UpstreamServiceError

# ── _pool_status unit tests ───────────────────────────────────────────────────

class TestPoolStatus:
    def test_below_normal(self):
        assert _pool_status(520.0, 552.0, 525.0, 590.0) == "below_normal"

    def test_normal(self):
        # at top_of_normal
        assert _pool_status(525.0, 552.0, 525.0, 590.0) == "normal"
        # between normal and conservation
        assert _pool_status(540.0, 552.0, 525.0, 590.0) == "normal"

    def test_above_conservation(self):
        # strictly above top_of_conservation
        assert _pool_status(552.1, 552.0, 525.0, 590.0) == "above_conservation"
        # between conservation and flood
        assert _pool_status(570.0, 552.0, 525.0, 590.0) == "above_conservation"
        # exactly at top_of_conservation is still "normal" (pool just full)
        assert _pool_status(552.0, 552.0, 525.0, 590.0) == "normal"

    def test_at_or_above_flood(self):
        assert _pool_status(590.0, 552.0, 525.0, 590.0) == "at_or_above_flood"
        assert _pool_status(600.0, 552.0, 525.0, 590.0) == "at_or_above_flood"

    def test_no_conservation_level_uses_normal_as_lower_bound(self):
        """Patoka has no top_of_conservation; status should still resolve.

        When conservation is None, conservation_ref == top_of_normal.
        'above_conservation' requires elev > conservation_ref (strict), so
        exactly at top_of_normal is still 'normal'.
        """
        # below normal → below_normal
        assert _pool_status(530.0, None, 536.0, 548.0) == "below_normal"
        # exactly at top_of_normal → normal (conservation_ref == normal here)
        assert _pool_status(536.0, None, 536.0, 548.0) == "normal"
        # strictly above normal (no conservation defined) → above_conservation
        assert _pool_status(542.0, None, 536.0, 548.0) == "above_conservation"
        # at flood → at_or_above_flood
        assert _pool_status(548.0, None, 536.0, 548.0) == "at_or_above_flood"

    def test_unknown_when_levels_missing(self):
        assert _pool_status(550.0, None, None, None) == "unknown"


# ── Lake table sanity checks ──────────────────────────────────────────────────

class TestLakeTable:
    def test_all_20_lakes_loaded(self):
        assert len(_LAKES) == 20

    def test_known_lake_present(self):
        assert "Barren" in _LAKES
        assert _LAKES["Barren"]["top_of_conservation_ft"] == 552.0
        assert _LAKES["Barren"]["top_of_flood_ft"] == 590.0

    def test_patoka_has_no_conservation_level(self):
        assert _LAKES["Patoka"]["top_of_conservation_ft"] is None
        assert _LAKES["Patoka"]["top_of_normal_ft"] == 536.0

    def test_lake_name_enum_contains_all_lakes(self):
        for lid in _LAKES:
            assert LakeName(lid).value == lid  # type: ignore[call-arg]


# ── Mocked integration tests ──────────────────────────────────────────────────

def _make_fake_cwms_response(elev_ft: float, ts_ms: int = 1791435600000) -> dict:
    """Build a minimal CWMS timeseries JSON response."""
    return {
        "name": "Barren.Elev.Inst.0.0.lrldlb-rev",
        "units": "ft",
        "values": [[ts_ms, elev_ft, 3]],
    }


@pytest.mark.asyncio
async def test_get_lake_conditions_normal_pool():
    """Normal-pool observation → status 'normal', correct deviation."""
    fake = _make_fake_cwms_response(elev_ft=540.0)

    with patch.object(lc_mod, "_cwms_get", new=AsyncMock(return_value=fake)):
        mcp = FastMCP("test")
        lc_mod.register(mcp)
        tools = {t.name: t for t in await mcp.list_tools()}
        assert "get_lake_conditions" in tools

        result = await lc_mod.get_lake_conditions(
            lake=LakeName("Barren"),  # type: ignore[call-arg]
        )

    assert result["lake_id"] == "Barren"
    assert result["elevation_ft"] == 540.0
    assert result["pool_status"] == "normal"
    # 540 - 552 (conservation) = -12
    assert result["deviation_from_conservation_ft"] == -12.0
    assert result["reference_levels"]["top_of_conservation_ft"] == 552.0
    assert "error" not in result


@pytest.mark.asyncio
async def test_get_lake_conditions_above_conservation():
    fake = _make_fake_cwms_response(elev_ft=560.0)

    with patch.object(lc_mod, "_cwms_get", new=AsyncMock(return_value=fake)):
        result = await lc_mod.get_lake_conditions(
            lake=LakeName("Barren"),  # type: ignore[call-arg]
        )

    assert result["pool_status"] == "above_conservation"
    assert result["deviation_from_conservation_ft"] == 8.0


@pytest.mark.asyncio
async def test_get_lake_conditions_no_data_when_values_empty():
    fake = {"name": "Barren.Elev.Inst.0.0.lrldlb-rev", "units": "ft", "values": []}

    with patch.object(lc_mod, "_cwms_get", new=AsyncMock(return_value=fake)):
        result = await lc_mod.get_lake_conditions(
            lake=LakeName("Barren"),  # type: ignore[call-arg]
        )

    assert result["pool_status"] == "no_data"
    assert result["elevation_ft"] is None
    assert "error" in result


@pytest.mark.asyncio
async def test_get_lake_conditions_upstream_error_returns_error_dict():
    err = UpstreamServiceError("CWMS API returned status 503.")
    with patch.object(lc_mod, "_cwms_get", new=AsyncMock(side_effect=err)):
        result = await lc_mod.get_lake_conditions(
            lake=LakeName("Barren"),  # type: ignore[call-arg]
        )

    assert result["pool_status"] == "no_data"
    assert result["elevation_ft"] is None
    assert result["error"] == "CWMS API returned status 503."


@pytest.mark.asyncio
async def test_cwms_get_sends_versioned_accept_header():
    """_cwms_get must send Accept: application/json;version=2.

    Without this header the CWMS API returns 501 Not Implemented.
    The test intercepts the outgoing httpx request and asserts the header
    is present before any network I/O occurs.
    """
    import httpx

    import example_server.tools.lake_conditions as lc_mod2
    from example_server.tools.lake_conditions import CWMS_ACCEPT

    captured_headers: dict[str, str] = {}

    async def mock_send(request: httpx.Request, **_kwargs):
        captured_headers.update(dict(request.headers))
        body = b'{"values":[]}'
        return httpx.Response(200, content=body, request=request)

    transport = httpx.MockTransport(mock_send)
    real_client_cls = httpx.AsyncClient

    def patched_client(**kw):
        kw.pop("trust_env", None)
        kw["transport"] = transport
        return real_client_cls(**kw)

    with patch.object(lc_mod2.httpx, "AsyncClient", side_effect=patched_client):
        try:
            await lc_mod2._cwms_get(
                "timeseries",
                {
                    "name": "Barren.Elev.Inst.0.0.lrldlb-rev",
                    "office": "LRL",
                    "unit": "ft",
                },
            )
        except Exception:
            pass  # We only care about captured headers.

    assert "accept" in captured_headers, "Accept header was not sent"
    assert captured_headers["accept"] == CWMS_ACCEPT, (
        f"Expected Accept: {CWMS_ACCEPT!r}, got {captured_headers['accept']!r}"
    )


@pytest.mark.asyncio
async def test_cwms_get_501_error_message_indicates_request_format():
    """A 501 from CWMS should produce an error hint about request format,
    not a generic 'service offline' message."""
    import httpx

    import example_server.tools.lake_conditions as lc_mod2
    from example_server.utils import UpstreamServiceError

    async def mock_send(request: httpx.Request, **_kwargs):
        return httpx.Response(
            501, content=b'{"message":"Not Implemented"}', request=request
        )

    transport = httpx.MockTransport(mock_send)
    real_client_cls = httpx.AsyncClient

    def patched_client(**kw):
        kw.pop("trust_env", None)
        kw["transport"] = transport
        return real_client_cls(**kw)

    with patch.object(lc_mod2.httpx, "AsyncClient", side_effect=patched_client):
        with pytest.raises(UpstreamServiceError) as exc_info:
            await lc_mod2._cwms_get(
                "timeseries",
                {"name": "Taylorsville.Elev.Inst.0.0.lrldlb-rev"},
            )

    assert "501" in str(exc_info.value)
    assert "request format" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_get_lake_conditions_patoka_no_conservation_level():
    """Patoka has no Top of Conservation; tool should still return a valid status.
    At exactly top_of_normal (536) → 'normal'; above → 'above_conservation'.
    """
    fake = _make_fake_cwms_response(elev_ft=542.0)

    with patch.object(lc_mod, "_cwms_get", new=AsyncMock(return_value=fake)):
        result = await lc_mod.get_lake_conditions(
            lake=LakeName("Patoka"),  # type: ignore[call-arg]
        )

    # 542 > 536 (top_of_normal, which doubles as conservation_ref) → above_conservation
    assert result["pool_status"] == "above_conservation"
    assert result["reference_levels"]["top_of_conservation_ft"] is None
    assert result["elevation_ft"] == 542.0


# ── Server-level registration smoke test ─────────────────────────────────────

@pytest.mark.asyncio
async def test_lake_conditions_tool_is_discoverable():
    from example_server.app import mcp

    tools = await mcp.list_tools()
    tool_names = {t.name for t in tools}
    assert "get_lake_conditions" in tool_names
