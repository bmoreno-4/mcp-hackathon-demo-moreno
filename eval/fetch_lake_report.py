#!/usr/bin/env python3
"""Download the USACE LRL Daily Lake Report and save as structured CSV + raw HTML.

Usage:
    uv run python eval/fetch_lake_report.py

Output (written to eval/reports/):
    lrl_lake_report_YYYY-MM-DD.csv   – parsed table; will NOT overwrite an existing file
    lrl_lake_report_YYYY-MM-DD.html  – raw HTML for provenance

CSV columns:
    lake, basin, winter_pool, summer_pool, flood_pool, todays_pool,
    dev_from_pool, change_24hr, inflow_24hr, outflow, percent_util

Exit code:
    0  success (or file already exists)
    1  fetch / parse error
"""

from __future__ import annotations

import csv
import datetime
import pathlib
import re
import ssl
import sys
import urllib.request

import certifi

REPORT_URL = "https://www.lrl-wc.usace.army.mil/reports/lkreport.html"
OUT_DIR = pathlib.Path(__file__).parent / "reports"

# Column indices in the HTML table (0-based after the header row)
# Basin(0), Project(1), Winter(2), Summer(3), Flood(4), Today(5),
# Dev(6), Change24(7), Precip24(8), Inflow24(9), Outflow(10),
# InchesRO_flood(11), PctUtil(12), InchesRO_guide(13)
COL_BASIN = 0
COL_PROJECT = 1
COL_WINTER = 2
COL_SUMMER = 3
COL_FLOOD = 4
COL_TODAY = 5
COL_DEV = 6
COL_CHANGE24 = 7
# COL_PRECIP24 = 8  – not written to CSV
COL_INFLOW24 = 9
COL_OUTFLOW = 10
# COL_INCHES_FLOOD = 11 – not written to CSV
COL_PCT_UTIL = 12
# COL_INCHES_GUIDE = 13 – not written to CSV

CSV_FIELDS = [
    "lake",
    "basin",
    "winter_pool",
    "summer_pool",
    "flood_pool",
    "todays_pool",
    "dev_from_pool",
    "change_24hr",
    "inflow_24hr",
    "outflow",
    "percent_util",
]


# The report server omits its DigiCert intermediate certificate; the package
# ships that public certificate (see SECURITY.md). Add it to the normal trust
# store instead of turning verification off.
REPORT_CA_FILE = (
    pathlib.Path(__file__).parent.parent
    / "src"
    / "lrl_reservoirs"
    / "data"
    / "digicert_global_g2_tls_rsa_sha256_2020_ca1.pem"
)


def _ssl_context() -> ssl.SSLContext:
    """Verified TLS: certifi roots plus the bundled DigiCert intermediate."""
    ctx = ssl.create_default_context(cafile=certifi.where())
    ctx.load_verify_locations(cafile=str(REPORT_CA_FILE))
    return ctx


def _fetch_html() -> str:
    """Download the report page with certificate and hostname checks on."""
    ctx = _ssl_context()
    req = urllib.request.Request(
        REPORT_URL,
        headers={"User-Agent": "Mozilla/5.0 (eval/fetch_lake_report.py)"},
    )
    with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _parse_date(html: str) -> datetime.date:
    """Extract the report date from the caption, e.g. '9  October   2026'."""
    m = re.search(
        r"(\d{1,2})\s+(\w+)\s+(\d{4})",
        html,
        re.IGNORECASE,
    )
    if not m:
        raise ValueError("Could not find a date in the report caption")
    day, month_str, year = int(m.group(1)), m.group(2), int(m.group(3))
    # Parse month name robustly
    month = datetime.datetime.strptime(month_str[:3], "%b").month
    return datetime.date(year, month, day)


def _cell_text(td_html: str) -> str:
    """Strip all HTML tags and whitespace from a <td> cell."""
    return re.sub(r"<[^>]+>", "", td_html).strip()


EXPECTED_LAKE_COUNT = 17


def _parse_rows(html: str) -> list[dict[str, str]]:
    """Parse data rows from the lake-report HTML table.

    The table has one header row followed by exactly 17 data rows.  Basin cells
    may be empty when a basin spans multiple projects; we carry the last
    non-empty value forward.

    Raises ValueError if the parsed row count is not exactly 17.
    """
    # Isolate everything inside <table>…</table>
    table_m = re.search(r"<table\b[^>]*>(.*?)</table>", html, re.IGNORECASE | re.DOTALL)
    if not table_m:
        raise ValueError("No <table> found in the report HTML")
    table_html = table_m.group(1)

    # Split into rows
    rows_raw = re.findall(
        r"<tr\b[^>]*>(.*?)</tr>", table_html, re.IGNORECASE | re.DOTALL
    )
    if not rows_raw:
        raise ValueError("No <tr> rows found inside the table")

    # Skip the header row (contains <th> elements)
    data_rows = [r for r in rows_raw if not re.search(r"<th\b", r, re.IGNORECASE)]

    records: list[dict[str, str]] = []
    last_basin = ""

    for row_html in data_rows:
        cells = re.findall(
            r"<td\b[^>]*>(.*?)</td>", row_html, re.IGNORECASE | re.DOTALL
        )
        if len(cells) < 13:
            continue  # skip malformed / footer rows

        basin_raw = _cell_text(cells[COL_BASIN])
        if basin_raw:
            last_basin = basin_raw

        lake = _cell_text(cells[COL_PROJECT])
        if not lake:
            continue

        records.append(
            {
                "lake": lake,
                "basin": last_basin,
                "winter_pool": _cell_text(cells[COL_WINTER]),
                "summer_pool": _cell_text(cells[COL_SUMMER]),
                "flood_pool": _cell_text(cells[COL_FLOOD]),
                "todays_pool": _cell_text(cells[COL_TODAY]),
                "dev_from_pool": _cell_text(cells[COL_DEV]),
                "change_24hr": _cell_text(cells[COL_CHANGE24]),
                "inflow_24hr": _cell_text(cells[COL_INFLOW24]),
                "outflow": _cell_text(cells[COL_OUTFLOW]),
                "percent_util": _cell_text(cells[COL_PCT_UTIL]),
            }
        )

    if len(records) != EXPECTED_LAKE_COUNT:
        raise ValueError(
            f"Expected {EXPECTED_LAKE_COUNT} lake rows, parsed {len(records)}. "
            "The report format may have changed."
        )
    return records


def main() -> int:
    print(f"Fetching {REPORT_URL} …")
    try:
        html = _fetch_html()
    except Exception as exc:
        print(f"ERROR fetching report: {exc}", file=sys.stderr)
        return 1

    try:
        report_date = _parse_date(html)
    except ValueError as exc:
        print(f"ERROR parsing date: {exc}", file=sys.stderr)
        return 1

    date_str = report_date.isoformat()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    csv_path = OUT_DIR / f"lrl_lake_report_{date_str}.csv"
    html_path = OUT_DIR / f"lrl_lake_report_{date_str}.html"

    if csv_path.exists():
        print(f"Already exists, skipping: {csv_path}")
        return 0

    try:
        records = _parse_rows(html)
    except ValueError as exc:
        print(f"ERROR parsing table: {exc}", file=sys.stderr)
        return 1

    if not records:
        print("ERROR: no data rows parsed from the report table", file=sys.stderr)
        return 1

    # Write CSV
    with open(csv_path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(records)
    print(f"Saved CSV  → {csv_path}  ({len(records)} lakes)")

    # Write raw HTML for provenance
    with open(html_path, "w", encoding="utf-8") as fh:
        fh.write(html)
    print(f"Saved HTML → {html_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
