"""Shared helpers: CWMS HTTP client, response formatting, and pagination.

Import these from your tool modules instead of re-implementing them. Keeping
I/O and formatting here keeps each tool file focused on its own logic.
"""

from __future__ import annotations

import asyncio
import functools
import json
import pathlib
import ssl
from typing import Any
from urllib.parse import unquote, urlsplit

import certifi
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

# Official USACE LRL Daily Lake Report URL — used in tool data_note strings and
# fetched (fixed URL, never a tool argument) by check_against_daily_report.
LAKE_REPORT_URL = "https://www.lrl-wc.usace.army.mil/reports/lkreport.html"

# Cap concurrent outbound HTTP requests so a district-wide fan-out (17 lakes ×
# 5 sub-requests each) cannot open hundreds of connections simultaneously.
_CWMS_SEMAPHORE = asyncio.Semaphore(8)


class UpstreamServiceError(RuntimeError):
    """Sanitized external-service failure safe to return to an MCP client."""


_BAD_PATH_MSG = "path must be a relative CWMS path without a query or fragment"


def _validate_cwms_path(path: str) -> str:
    """Reject path segments that could escape or override CWMS_BASE_URL.

    Accepts only non-empty relative paths with no scheme, authority, query,
    fragment, leading slash, backslash, or dot-dot traversal — including
    percent-encoded and double-encoded variants.

    Raises:
        ValueError: with a message containing "relative CWMS path".
    """
    if not path:
        raise ValueError(_BAD_PATH_MSG)

    parsed = urlsplit(path)

    # Reject anything with a scheme (http://, https://) or authority (//host).
    if parsed.scheme or parsed.netloc:
        raise ValueError(_BAD_PATH_MSG)

    # Reject query strings and fragments.
    if parsed.query or parsed.fragment:
        raise ValueError(_BAD_PATH_MSG)

    # Iteratively percent-decode the path portion up to 3 times to catch
    # double-encoding tricks (%252e%252e → %2e%2e → ..).
    decoded = parsed.path
    for _ in range(3):
        next_decoded = unquote(decoded)
        if next_decoded == decoded:
            break
        decoded = next_decoded

    # Normalise backslashes and check each segment.
    segments = decoded.replace("\\", "/").split("/")
    if decoded.startswith(("/", "\\")):
        raise ValueError(_BAD_PATH_MSG)
    if any(seg in {".", ".."} for seg in segments):
        raise ValueError(_BAD_PATH_MSG)

    return path


async def cwms_get(path: str, params: dict[str, str]) -> Any:
    """GET a CWMS Data API path and return parsed JSON.

    path must be a relative path segment (no leading slash, no scheme/host,
    no dot-dot traversal).  The path is validated before any HTTP client is
    created, so invalid inputs are rejected without making a network call.

    Uses a module-level asyncio.Semaphore(8) to cap concurrent HTTP requests.

    Raises:
        ValueError: if path could escape or override the CWMS base URL.
        UpstreamServiceError: with a sanitized message for any network or
            HTTP-level failure.
    """
    _validate_cwms_path(path)
    async with _CWMS_SEMAPHORE:
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


# The LRL report server sends only its own certificate, not the DigiCert
# intermediate that links it to a trusted root, so standard verification fails
# ("unable to get local issuer certificate"). Ship that public intermediate and
# add it to the normal trust store for this one client. Verification, including
# the hostname check, stays on. The file is checked in tests to chain to the
# DigiCert Global Root G2 in certifi.
REPORT_CA_FILE = (
    pathlib.Path(__file__).parent
    / "data"
    / "digicert_global_g2_tls_rsa_sha256_2020_ca1.pem"
)


@functools.lru_cache(maxsize=1)
def lake_report_ssl_context() -> ssl.SSLContext:
    """Default trust store (certifi) plus the report server's intermediate CA."""
    ctx = ssl.create_default_context(cafile=certifi.where())
    if REPORT_CA_FILE.exists():
        ctx.load_verify_locations(cafile=str(REPORT_CA_FILE))
    return ctx


async def fetch_lake_report_html() -> str:
    """GET the LRL Daily Lake Report page and return its HTML text.

    Second narrowly scoped client: the URL is the fixed LAKE_REPORT_URL
    constant, redirects are not followed, TLS is verified (with the bundled
    DigiCert intermediate, see REPORT_CA_FILE), and the body is capped at
    MAX_RESPONSE_BYTES. Failures raise UpstreamServiceError with a
    sanitized message.
    """
    try:
        async with httpx.AsyncClient(
            follow_redirects=False,
            timeout=DEFAULT_TIMEOUT,
            trust_env=False,
            verify=lake_report_ssl_context(),
            headers={"Accept": "text/html"},
        ) as client:
            async with client.stream("GET", LAKE_REPORT_URL) as response:
                response.raise_for_status()
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > MAX_RESPONSE_BYTES:
                        raise UpstreamServiceError(
                            "Lake report response exceeded the size limit."
                        )
                return content.decode("utf-8", errors="replace")
    except UpstreamServiceError:
        raise
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        raise UpstreamServiceError(
            f"LRL lake report returned status {status}."
        ) from None
    except httpx.RequestError:
        raise UpstreamServiceError("LRL lake report request failed.") from None


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
