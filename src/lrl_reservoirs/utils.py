"""Shared helpers: CWMS HTTP client, response formatting, and pagination.

Import these from your tool modules instead of re-implementing them. Keeping
I/O and formatting here keeps each tool file focused on its own logic.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import unquote, urlsplit

import httpx

# Federal APIs can be slow or intermittently unresponsive — always use an
# explicit timeout. Bump this for large exports.
DEFAULT_TIMEOUT = 20.0
MAX_RESPONSE_BYTES = 1_000_000

# CWMS Data API base URL — keep outbound origins operator-controlled.
# Do not replace with a tool argument; add another narrowly scoped client
# when integrating another API.
CWMS_BASE_URL = "https://cwms-data.usace.army.mil/cwms-data/"

# CWMS Data API requires this header to negotiate the v2 JSON response format.
# Without it (or with Accept: */*), the server returns HTTP 501 Not Implemented.
# The endpoint does not issue redirects; follow_redirects=False is correct.
CWMS_ACCEPT = "application/json;version=2"


class UpstreamServiceError(RuntimeError):
    """Sanitized external-service failure safe to return to an MCP client."""


def _validate_relative_path(path: str) -> str:
    """Reject paths that could select or escape the configured API origin."""
    parsed = urlsplit(path)
    decoded_path = parsed.path
    for _ in range(3):
        decoded = unquote(decoded_path)
        if decoded == decoded_path:
            break
        decoded_path = decoded
    segments = decoded_path.replace("\\", "/").split("/")
    if (
        not path
        or parsed.scheme
        or parsed.netloc
        or parsed.query
        or parsed.fragment
        or decoded_path.startswith(("/", "\\"))
        or any(segment in {".", ".."} for segment in segments)
    ):
        raise ValueError("path must be a relative API path without a query or fragment")
    return path


async def cwms_get(path: str, params: dict[str, str]) -> Any:
    """GET a CWMS Data API path and return parsed JSON.

    path must be a relative path segment (no leading slash, no scheme/host).
    Raises UpstreamServiceError with a sanitized message on any failure.
    """
    try:
        async with httpx.AsyncClient(
            base_url=CWMS_BASE_URL,
            follow_redirects=False,
            timeout=DEFAULT_TIMEOUT,
            trust_env=False,
            headers={"Accept": CWMS_ACCEPT},
        ) as client:
            async with client.stream("GET", path, params=params) as response:
                response.raise_for_status()
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > MAX_RESPONSE_BYTES:
                        raise UpstreamServiceError(
                            "Upstream response exceeded the size limit."
                        )
                return json.loads(content)
    except UpstreamServiceError:
        raise
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status == 501:
            raise UpstreamServiceError(
                f"CWMS API returned status {status}: request format not accepted "
                f"(check Accept header and query parameters)."
            ) from None
        raise UpstreamServiceError(f"CWMS API returned status {status}.") from None
    except (httpx.DecodingError, json.JSONDecodeError, UnicodeDecodeError):
        raise UpstreamServiceError("CWMS API returned invalid JSON.") from None
    except httpx.RequestError:
        raise UpstreamServiceError("CWMS API request failed.") from None


def paginate(items: list[Any], limit: int, offset: int) -> dict[str, Any]:
    """Slice a list and return a standard pagination envelope.

    Returns a dict with `total`, `count`, `offset`, `items`, `has_more`, and
    `next_offset` — the shape recommended in the MCP best-practices guide.
    """
    total = len(items)
    window = items[offset : offset + limit]
    next_offset = offset + len(window)
    return {
        "total": total,
        "count": len(window),
        "offset": offset,
        "items": window,
        "has_more": next_offset < total,
        "next_offset": next_offset if next_offset < total else None,
    }
