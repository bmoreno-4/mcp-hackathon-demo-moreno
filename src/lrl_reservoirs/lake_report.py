"""Parser for the USACE LRL Daily Lake Report (lkreport.html).

The report is a single HTML table published each morning (06:00 Eastern) with
one row per flood risk management lake. The Project column uses the same CWMS
location IDs as this server (e.g. "CaesarCreek", "WHHarsha").

This is the runtime copy of the parser in eval/fetch_lake_report.py; both are
tested against the saved 2026-10-09 report HTML.
"""

from __future__ import annotations

import datetime
import re
from typing import Any
from zoneinfo import ZoneInfo

EXPECTED_LAKE_COUNT = 17
REPORT_TIMEZONE = ZoneInfo("America/New_York")
REPORT_HOUR_LOCAL = 6  # observations in the report are for 06:00 Eastern

# Column order in the report table (0-based):
# Basin, Project, Winter, Summer, Flood, Today, Dev, Change24, Precip24,
# Inflow24, Outflow, InchesRO_flood, PctUtil, InchesRO_guide
_COLUMNS = {
    "basin": 0,
    "lake": 1,
    "winter_pool_ft": 2,
    "summer_pool_ft": 3,
    "flood_pool_ft": 4,
    "todays_pool_ft": 5,
    "dev_from_pool_ft": 6,
    "change_24hr_ft": 7,
    "precip_24hr_in": 8,
    "inflow_24hr_cfs": 9,
    "outflow_6am_cfs": 10,
    "percent_util": 12,
}
_MIN_CELLS = 13


def _cell_text(td_html: str) -> str:
    return re.sub(r"<[^>]+>", "", td_html).strip()


def _to_float(text: str) -> float | None:
    try:
        return float(text.replace(",", ""))
    except ValueError:
        return None


def parse_report_date(html: str) -> datetime.date:
    """Return the report date from the caption, e.g. '9  October   2026'."""
    m = re.search(
        r"(\d{1,2})\s+(January|February|March|April|May|June|July|August|"
        r"September|October|November|December)\s+(\d{4})",
        html,
        re.IGNORECASE,
    )
    if not m:
        raise ValueError("Could not find a date in the report caption")
    month = datetime.datetime.strptime(m.group(2)[:3].title(), "%b").month
    return datetime.date(int(m.group(3)), month, int(m.group(1)))


def report_reference_time(report_date: datetime.date) -> datetime.datetime:
    """06:00 Eastern on the report date, as an aware UTC datetime."""
    local = datetime.datetime.combine(
        report_date, datetime.time(REPORT_HOUR_LOCAL), tzinfo=REPORT_TIMEZONE
    )
    return local.astimezone(datetime.timezone.utc)


def parse_report_rows(html: str) -> dict[str, dict[str, Any]]:
    """Parse the lake table into {lake_id: row}. Numeric cells become floats.

    Blank basin cells inherit the basin above. Raises ValueError unless exactly
    17 lake rows are found, so a format change fails loudly.
    """
    table_m = re.search(r"<table\b[^>]*>(.*?)</table>", html, re.IGNORECASE | re.DOTALL)
    if not table_m:
        raise ValueError("No <table> found in the report HTML")
    rows_raw = re.findall(
        r"<tr\b[^>]*>(.*?)</tr>", table_m.group(1), re.IGNORECASE | re.DOTALL
    )
    records: dict[str, dict[str, Any]] = {}
    last_basin = ""
    for row_html in rows_raw:
        if re.search(r"<th\b", row_html, re.IGNORECASE):
            continue
        cells = re.findall(
            r"<td\b[^>]*>(.*?)</td>", row_html, re.IGNORECASE | re.DOTALL
        )
        if len(cells) < _MIN_CELLS:
            continue
        basin = _cell_text(cells[_COLUMNS["basin"]]) or last_basin
        last_basin = basin
        lake = _cell_text(cells[_COLUMNS["lake"]])
        if not lake:
            continue
        row: dict[str, Any] = {"basin": basin}
        for key, idx in _COLUMNS.items():
            if key in ("basin", "lake"):
                continue
            row[key] = _to_float(_cell_text(cells[idx]))
        records[lake] = row

    if len(records) != EXPECTED_LAKE_COUNT:
        raise ValueError(
            f"Expected {EXPECTED_LAKE_COUNT} lake rows, parsed {len(records)}. "
            "The report format may have changed."
        )
    return records
