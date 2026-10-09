"""Tests for eval/fetch_lake_report.py — HTML parsing against the saved fixture.

Uses the provenance HTML saved alongside the 2026-10-09 CSV to verify that
_parse_rows produces exactly 17 rows with the correct values for a sample of
lakes, and that it raises loudly when the row count is wrong.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

# Make the eval/ directory importable without installing it as a package.
_EVAL_DIR = pathlib.Path(__file__).parent.parent / "eval"
if str(_EVAL_DIR) not in sys.path:
    sys.path.insert(0, str(_EVAL_DIR))

from fetch_lake_report import EXPECTED_LAKE_COUNT, _parse_rows  # noqa: E402

FIXTURE_HTML = (
    pathlib.Path(__file__).parent.parent
    / "eval"
    / "reports"
    / "lrl_lake_report_2026-10-09.html"
)


@pytest.fixture(scope="module")
def parsed_rows() -> list[dict[str, str]]:
    html = FIXTURE_HTML.read_text(encoding="utf-8")
    return _parse_rows(html)


# ── Row-count guard ───────────────────────────────────────────────────────────


def test_row_count(parsed_rows: list[dict[str, str]]) -> None:
    """Exactly 17 lake rows must be parsed."""
    assert len(parsed_rows) == EXPECTED_LAKE_COUNT


# ── Spot-checks for three lakes ───────────────────────────────────────────────


def test_caesarcreek_values(parsed_rows: list[dict[str, str]]) -> None:
    row = next(r for r in parsed_rows if r["lake"] == "CaesarCreek")
    assert row["basin"] == "Little Miami"
    assert row["winter_pool"] == "846.0"
    assert row["summer_pool"] == "849.0"
    assert row["flood_pool"] == "883.0"
    assert row["todays_pool"] == "847.8"
    assert row["dev_from_pool"] == "0.1"
    assert row["percent_util"] == "0.07"


def test_patoka_values(parsed_rows: list[dict[str, str]]) -> None:
    """Patoka has the highest percent util and a non-trivial dev_from_pool."""
    row = next(r for r in parsed_rows if r["lake"] == "Patoka")
    assert row["todays_pool"] == "538.6"
    assert row["dev_from_pool"] == "2.9"
    assert row["percent_util"] == "21.23"


def test_taylorsville_values(parsed_rows: list[dict[str, str]]) -> None:
    """Taylorsville has a negative dev_from_pool and negative percent_util."""
    row = next(r for r in parsed_rows if r["lake"] == "Taylorsville")
    assert row["basin"] == "Salt River"
    assert row["dev_from_pool"] == "-0.5"
    assert row["percent_util"] == "-0.76"


# ── All lakes present ─────────────────────────────────────────────────────────


def test_all_expected_lakes_present(parsed_rows: list[dict[str, str]]) -> None:
    expected = {
        "CaesarCreek", "WHHarsha", "WestFork", "CJBrown", "Brookville",
        "CaveRun", "CarrCreek", "Buckhorn", "Taylorsville", "Green",
        "Nolin", "Barren", "Rough", "CMHarden", "CaglesMill", "Monroe", "Patoka",
    }
    found = {r["lake"] for r in parsed_rows}
    assert found == expected


# ── Error path: wrong row count raises ValueError ─────────────────────────────


def test_parse_rows_raises_on_no_table() -> None:
    """HTML with no <table> element must raise a ValueError."""
    with pytest.raises(ValueError, match="No <table> found"):
        _parse_rows("<html><body>no table here</body></html>")


def test_parse_rows_raises_on_missing_rows() -> None:
    """HTML with only 3 data rows must raise a ValueError mentioning the count."""
    stub = """
    <table border>
      <tr><th>Basin</th><th>Project</th></tr>
      <tr><td>Little Miami</td><td>CaesarCreek</td><td>846</td><td>849</td>
          <td>883</td><td>847.8</td><td>0.1</td><td>0.0</td><td>0.0</td>
          <td>59</td><td>80</td><td>11.36</td><td>0.07</td><td>0.00</td></tr>
    </table>
    """
    with pytest.raises(ValueError, match="Expected 17 lake rows, parsed 1"):
        _parse_rows(stub)
