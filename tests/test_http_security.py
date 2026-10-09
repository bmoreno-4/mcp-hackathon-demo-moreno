"""Regression tests for the CWMS outbound request boundary."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from lrl_reservoirs import utils


@pytest.mark.parametrize(
    "path",
    [
        "http://169.254.169.254/latest/meta-data",
        "https://localhost/admin",
        "//127.0.0.1/admin",
        "/absolute/path",
        "../admin",
        "datasets/../../admin",
        "%2e%2e/admin",
        "%252e%252e/admin",
        "%2f%2f127.0.0.1/admin",
        "datasets?target=http://127.0.0.1",
        "datasets#fragment",
        "\\\\127.0.0.1\\admin",
        "",
    ],
)
def test_validate_relative_path_rejects_destination_override(path: str) -> None:
    with pytest.raises(ValueError, match="relative API path"):
        utils._validate_relative_path(path)


def test_validate_relative_path_accepts_expected_api_path() -> None:
    assert utils._validate_relative_path("datasets/search") == "datasets/search"


class _FakeClient:
    """Capture client policy while returning a controlled upstream response."""

    init_kwargs: dict[str, Any] = {}
    response = httpx.Response(200, json={"values": []})

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

    def stream(self, method: str, path: str, **kwargs: Any) -> _Stream:
        self.response.request = httpx.Request(
            method, f"https://cwms-data.usace.army.mil/cwms-data/{path}"
        )
        return self._Stream(self.response)


@pytest.mark.asyncio
async def test_cwms_get_uses_fixed_origin_and_safe_client_policy(monkeypatch) -> None:
    monkeypatch.setattr(utils.httpx, "AsyncClient", _FakeClient)

    ts_id = "Barren.Elev.Inst.0.0.lrldlb-rev"
    assert await utils.cwms_get("timeseries", {"name": ts_id}) == {"values": []}
    assert _FakeClient.init_kwargs["base_url"] == utils.CWMS_BASE_URL
    assert _FakeClient.init_kwargs["follow_redirects"] is False
    assert _FakeClient.init_kwargs["trust_env"] is False
    assert _FakeClient.init_kwargs["headers"]["Accept"] == utils.CWMS_ACCEPT


@pytest.mark.asyncio
async def test_upstream_error_does_not_expose_sensitive_response(
    monkeypatch, caplog
) -> None:
    sensitive = "SSN 123-45-6789 DOB 1940-01-01 token=secret"
    _FakeClient.response = httpx.Response(500, text=sensitive)
    monkeypatch.setattr(utils.httpx, "AsyncClient", _FakeClient)

    with pytest.raises(utils.UpstreamServiceError) as captured:
        await utils.cwms_get("timeseries", {"name": "x"})

    message = str(captured.value)
    assert message == "CWMS API returned status 500."
    assert sensitive not in message
    assert sensitive not in caplog.text


@pytest.mark.asyncio
async def test_invalid_json_is_sanitized(monkeypatch) -> None:
    sensitive = "not-json SSN 123-45-6789"
    _FakeClient.response = httpx.Response(200, content=sensitive)
    monkeypatch.setattr(utils.httpx, "AsyncClient", _FakeClient)

    with pytest.raises(utils.UpstreamServiceError) as captured:
        await utils.cwms_get("timeseries", {"name": "x"})

    assert str(captured.value) == "CWMS API returned invalid JSON."
    assert sensitive not in str(captured.value)


@pytest.mark.asyncio
async def test_501_error_message_indicates_request_format(monkeypatch) -> None:
    _FakeClient.response = httpx.Response(501, json={"message": "Not Implemented"})
    monkeypatch.setattr(utils.httpx, "AsyncClient", _FakeClient)

    with pytest.raises(utils.UpstreamServiceError) as captured:
        await utils.cwms_get("timeseries", {"name": "x"})

    assert "501" in str(captured.value)
    assert "request format" in str(captured.value).lower()
