#!/usr/bin/env python3
"""Validation against the USACE LRL Daily Lake Report.

Reads the matching CSV from eval/reports/ and validates two things for every
lake against live CWMS data:

  1. Percent Util  – computed from CWMS storage vs. the report value
  2. Guide curve elevation – computed from CWMS seasonal level vs.
     (todays_pool - dev_from_pool) from the report

Usage:
    uv run python eval/lrl_percent_util_validation.py --date 2026-10-09

    # Refresh the test fixture (only works with --date 2026-10-08):
    uv run python eval/lrl_percent_util_validation.py --save-fixtures --date 2026-10-08

Requires a downloaded report CSV in eval/reports/.  If it is missing, run:
    uv run python eval/fetch_lake_report.py

Exit code:
    0  all 17 lakes within tolerance on both checks
    1  one or more lakes outside tolerance, or fetch errors
"""

from __future__ import annotations

import argparse
import calendar
import csv
import datetime
import json
import pathlib
import sys
import urllib.parse
import urllib.request

# ── Configuration ─────────────────────────────────────────────────────────────

CWMS_BASE = "https://cwms-data.usace.army.mil/cwms-data"
ACCEPT = "application/json;version=2"
OFFICE = "LRL"

# eval/reports/ directory (sibling of this script)
REPORTS_DIR = pathlib.Path(__file__).parent / "reports"

# Allowed difference between computed and reported values.
# Storage TS may lag the report time by up to one hour.
TOLERANCE_PCT_UTIL = 0.15   # percent-util points
TOLERANCE_ELEV_FT = 0.10    # feet (guide curve elevation; report rounds dev to 1 dp)

# Lake order matches the LRL Daily Lake Report
LAKES = [
    "CaesarCreek",
    "WHHarsha",
    "WestFork",
    "CJBrown",
    "Brookville",
    "CaveRun",
    "CarrCreek",
    "Buckhorn",
    "Taylorsville",
    "Green",
    "Nolin",
    "Barren",
    "Rough",
    "CMHarden",
    "CaglesMill",
    "Monroe",
    "Patoka",
]

# Storage TS suffix
STOR_TS_SUFFIX = "lrldlb-comp"

# ── CSV loader ────────────────────────────────────────────────────────────────


def load_report_csv(date_str: str) -> dict[str, dict[str, float]]:
    """Load eval/reports/lrl_lake_report_YYYY-MM-DD.csv into a keyed dict.

    Returns {lake_id: {percent_util, todays_pool, dev_from_pool}}.
    Raises FileNotFoundError when the CSV does not exist.
    """
    csv_path = REPORTS_DIR / f"lrl_lake_report_{date_str}.csv"
    if not csv_path.exists():
        raise FileNotFoundError(
            f"No report CSV found for {date_str}. "
            f"Run: uv run python eval/fetch_lake_report.py"
        )
    data: dict[str, dict[str, float]] = {}
    with open(csv_path, newline="") as fh:
        for row in csv.DictReader(fh):
            lake = row["lake"]
            try:
                data[lake] = {
                    "percent_util": float(row["percent_util"]),
                    "todays_pool": float(row["todays_pool"]),
                    "dev_from_pool": float(row["dev_from_pool"]),
                }
            except (KeyError, ValueError):
                pass  # skip rows with missing/non-numeric values
    return data


# ── Helpers ───────────────────────────────────────────────────────────────────


def cwms_fetch(url: str) -> dict:
    req = urllib.request.Request(url, headers={"Accept": ACCEPT})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())
    except Exception as exc:
        return {"_error": str(exc)[:120]}


def add_months(dt: datetime.datetime, months: int) -> datetime.datetime:
    m = dt.month - 1 + months
    year = dt.year + m // 12
    month = m % 12 + 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def resolve_level(
    data: dict, query: datetime.datetime
) -> tuple[float | None, str | None]:
    """Extract a scalar storage or elevation level from a CWMS response."""
    if "_error" in data:
        return None, data["_error"]
    if "constant-value" in data:
        return float(data["constant-value"]), None
    sv = data.get("seasonal-values", [])
    origin_str = data.get("interval-origin", "")
    months = int(data.get("interval-months", 12))
    if not sv or not origin_str:
        return None, "no seasonal-values"
    origin = datetime.datetime.fromisoformat(origin_str.replace("Z", "+00:00"))
    year_diff = query.year - origin.year
    start = max(0, year_diff - 1)
    anchors: list[tuple[datetime.datetime, float]] = []
    for n in range(start, start + 4):
        cyc = months * n
        for s in sv:
            base = add_months(origin, cyc + s["offset-months"])
            t = base + datetime.timedelta(minutes=s["offset-minutes"])
            anchors.append((t, s["value"]))
    anchors.sort()
    for i in range(len(anchors) - 1):
        t0, v0 = anchors[i]
        t1, v1 = anchors[i + 1]
        if t0 <= query <= t1:
            span = (t1 - t0).total_seconds()
            frac = (query - t0).total_seconds() / span if span else 0.0
            return v0 + frac * (v1 - v0), None
    return anchors[-1][1], None


def fetch_lake_data(
    lake: str,
    win_begin: str,
    win_end: str,
    level_date: str,
    query_utc: datetime.datetime,
) -> tuple[float | None, float | None, dict, str]:
    """Fetch CWMS data for *lake* and compute percent_util and guide_curve_ft.

    Returns (percent_util, guide_curve_elev_ft, raw_responses_dict, notes_string).
    """
    raw: dict = {}
    notes_parts: list[str] = []

    # ── Storage timeseries ────────────────────────────────────────────────────
    ts_id = f"{lake}.Stor.Inst.1Hour.0.{STOR_TS_SUFFIX}"
    ts_url = (
        f"{CWMS_BASE}/timeseries?name={urllib.parse.quote(ts_id, safe='')}"
        f"&office={OFFICE}&unit=ac-ft&begin={win_begin}&end={win_end}"
    )
    ts_d = cwms_fetch(ts_url)
    raw["stor_ts_response"] = ts_d
    vals = ts_d.get("values", [])
    cur_stor: float | None = None
    if vals:
        best = min(vals, key=lambda v: abs(v[0] - int(query_utc.timestamp() * 1000)))
        if best[1] is not None:
            cur_stor = float(best[1])

    # ── Storage at guide curve (Bottom of Flood Control) ─────────────────────
    gc_stor_url = (
        f"{CWMS_BASE}/levels/"
        f"{urllib.parse.quote(lake + '.Stor.Inst.0.Bottom of Flood Control', safe='')}"
        f"?office={OFFICE}&unit=ac-ft&effective-date={level_date}"
    )
    gc_stor_d = cwms_fetch(gc_stor_url)
    raw["gc_level_response"] = gc_stor_d
    gc_stor, gc_err = resolve_level(gc_stor_d, query_utc)

    # ── Storage at flood pool (Top of Flood) ─────────────────────────────────
    fl_url = (
        f"{CWMS_BASE}/levels/"
        f"{urllib.parse.quote(lake + '.Stor.Inst.0.Top of Flood', safe='')}"
        f"?office={OFFICE}&unit=ac-ft&effective-date={level_date}"
    )
    fl_d = cwms_fetch(fl_url)
    raw["flood_level_response"] = fl_d
    fl_stor, fl_err = resolve_level(fl_d, query_utc)

    # ── Guide curve elevation (Bottom of Flood Control, in feet) ─────────────
    gc_elev_url = (
        f"{CWMS_BASE}/levels/"
        f"{urllib.parse.quote(lake + '.Elev.Inst.0.Bottom of Flood Control', safe='')}"
        f"?office={OFFICE}&unit=ft&effective-date={level_date}"
    )
    gc_elev_d = cwms_fetch(gc_elev_url)
    raw["elev_gc_level_response"] = gc_elev_d
    gc_elev, gc_elev_err = resolve_level(gc_elev_d, query_utc)

    # ── Compute percent_util ──────────────────────────────────────────────────
    if cur_stor is None:
        notes_parts.append(f"no stor (vals={len(vals)})")
    if gc_stor is None:
        notes_parts.append(f"gc_stor_err={gc_err}")
    if fl_stor is None:
        notes_parts.append(f"fl_err={fl_err}")
    if gc_elev is None:
        notes_parts.append(f"gc_elev_err={gc_elev_err}")

    pct: float | None = None
    if cur_stor is not None and gc_stor is not None and fl_stor is not None:
        denom = fl_stor - gc_stor
        pct = round((cur_stor - gc_stor) / denom * 100, 2) if denom != 0 else None

    return pct, gc_elev, raw, "; ".join(notes_parts)


# ── Main ──────────────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--date",
        required=True,
        help="Report date YYYY-MM-DD (a matching CSV must exist in eval/reports/)",
    )
    parser.add_argument(
        "--save-fixtures",
        action="store_true",
        help=(
            "Overwrite tests/fixtures/lrl_oct8_2026/cwms_responses.json"
            " with freshly fetched data (only works with --date 2026-10-08)"
        ),
    )
    args = parser.parse_args()

    try:
        report_date = datetime.date.fromisoformat(args.date)
    except ValueError:
        print(f"ERROR: --date must be YYYY-MM-DD, got {args.date!r}", file=sys.stderr)
        return 1

    try:
        report_data = load_report_csv(args.date)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    # 06:00 US/Eastern = 10:00 UTC in October (EDT = UTC-4)
    query_utc = datetime.datetime(
        report_date.year,
        report_date.month,
        report_date.day,
        10,
        0,
        0,
        tzinfo=datetime.timezone.utc,
    )
    win_begin = (query_utc - datetime.timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S")
    win_end = (query_utc + datetime.timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S")
    level_date = query_utc.strftime("%Y-%m-%dT%H:%M:%S")

    print(
        f"\nLRL validation — report date {args.date}"
        f" (query UTC: {query_utc.isoformat()})"
    )
    print(f"Storage window: {win_begin} → {win_end} UTC\n")

    # ── Percent Util table ────────────────────────────────────────────────────
    print(f"{'Lake':<14} {'Rpt%':>7} {'Calc%':>7} {'ΔPct':>6} {'PctOK':>6}  "
          f"{'RptGC':>8} {'CalcGC':>8} {'ΔElev':>6} {'ElevOK':>6}  Notes")
    print("-" * 100)

    failures: list[str] = []
    all_raw: dict = {}

    for lake in LAKES:
        calc_pct, calc_gc, raw, notes = fetch_lake_data(
            lake, win_begin, win_end, level_date, query_utc
        )
        all_raw[lake] = raw

        row = report_data.get(lake)
        if row is None:
            print(f"{lake:<14}  {'N/A':>7} {'N/A':>7} {'N/A':>6} {'?':>6}  "
                  f"{'N/A':>8} {'N/A':>8} {'N/A':>6} {'?':>6}  no report value")
            continue

        rpt_pct = row.get("percent_util")
        rpt_gc: float | None = None
        todays_pool = row.get("todays_pool")
        dev_from_pool = row.get("dev_from_pool")
        if todays_pool is not None and dev_from_pool is not None:
            # guide_curve = today's pool elevation − deviation from pool
            rpt_gc = round(todays_pool - dev_from_pool, 2)

        # Percent util check
        if rpt_pct is not None and calc_pct is not None:
            pct_diff = calc_pct - rpt_pct
            pct_ok = abs(pct_diff) <= TOLERANCE_PCT_UTIL
            pct_flag = "PASS" if pct_ok else "FAIL"
            pct_rpt_s = f"{rpt_pct:>7.2f}"
            pct_calc_s = f"{calc_pct:>7.2f}"
            pct_diff_s = f"{pct_diff:>+6.2f}"
        elif rpt_pct is None:
            pct_ok, pct_flag = True, "N/A"
            pct_rpt_s = pct_calc_s = pct_diff_s = " N/A"
        else:
            pct_ok, pct_flag = False, "FAIL"
            pct_rpt_s = f"{rpt_pct:>7.2f}" if rpt_pct is not None else "   N/A"
            pct_calc_s, pct_diff_s = "    N/A", "   N/A"

        # Guide curve elevation check
        if rpt_gc is not None and calc_gc is not None:
            elev_diff = round(calc_gc - rpt_gc, 4)  # round away float precision noise
            elev_ok = abs(elev_diff) <= TOLERANCE_ELEV_FT
            elev_flag = "PASS" if elev_ok else "FAIL"
            gc_rpt_s = f"{rpt_gc:>8.2f}"
            gc_calc_s = f"{calc_gc:>8.2f}"
            elev_diff_s = f"{elev_diff:>+6.2f}"
        elif rpt_gc is None:
            elev_ok, elev_flag = True, "N/A"
            gc_rpt_s = gc_calc_s = elev_diff_s = "     N/A"
        else:
            elev_ok, elev_flag = False, "FAIL"
            gc_rpt_s = f"{rpt_gc:>8.2f}" if rpt_gc is not None else "     N/A"
            gc_calc_s, elev_diff_s = "     N/A", "    N/A"

        print(
            f"{lake:<14} {pct_rpt_s} {pct_calc_s} {pct_diff_s} {pct_flag:>6}  "
            f"{gc_rpt_s} {gc_calc_s} {elev_diff_s} {elev_flag:>6}  {notes}"
        )

        if not pct_ok or not elev_ok:
            failures.append(lake)

    print()
    n_lakes = len(LAKES)
    if failures:
        failed_str = ", ".join(failures)
        print(
            f"FAILED ({len(failures)}/{n_lakes} lakes outside tolerance): "
            f"{failed_str}\n"
            f"  Tolerances: percent_util ±{TOLERANCE_PCT_UTIL}, "
            f"guide curve elevation ±{TOLERANCE_ELEV_FT} ft"
        )
    else:
        print(
            f"All {n_lakes} lakes within tolerance  ✓\n"
            f"  (percent_util ±{TOLERANCE_PCT_UTIL}, "
            f"guide curve elevation ±{TOLERANCE_ELEV_FT} ft)"
        )

    if args.save_fixtures and args.date == "2026-10-08":
        print("\nRefreshing test fixtures …")
        # Elevation TS is already in all_raw from fetch_lake_data; write it out
        fixture_path = (
            pathlib.Path(__file__).parent.parent
            / "tests"
            / "fixtures"
            / "lrl_oct8_2026"
            / "cwms_responses.json"
        )
        fixture_path.parent.mkdir(parents=True, exist_ok=True)
        with open(fixture_path, "w") as f:
            json.dump(all_raw, f, indent=2)
        print(f"Saved → {fixture_path}")
    elif args.save_fixtures:
        print(
            "NOTE: --save-fixtures only writes the 2026-10-08 fixture"
            " (use --date 2026-10-08)"
        )

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
