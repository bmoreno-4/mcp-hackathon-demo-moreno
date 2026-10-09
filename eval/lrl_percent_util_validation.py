#!/usr/bin/env python3
"""Live percent_util validation against the USACE LRL Daily Lake Report.

Fetches real CWMS data for a specified report date and computes percent_util
for all 17 LRL lakes using the same formula as get_lake_conditions, then
compares against the saved LRL Daily Lake Report values.

Usage:
    # Compare against the bundled 2026-10-08 report (default):
    python eval/lrl_percent_util_validation.py

    # Compare against a different report date (must be a past date with CWMS data):
    python eval/lrl_percent_util_validation.py --date 2026-10-01

    # Refresh the test fixture with the default date's responses:
    python eval/lrl_percent_util_validation.py --save-fixtures

The script also optionally updates tests/fixtures/lrl_oct8_2026/cwms_responses.json
when run with --save-fixtures, giving you a way to regenerate the deterministic
test data if the formula or the CWMS response shape ever changes.

Exit code:
    0  all 17 lakes within tolerance
    1  one or more lakes outside tolerance, or fetch errors
"""

from __future__ import annotations

import argparse
import calendar
import datetime
import json
import pathlib
import sys
import urllib.parse
import urllib.request

# ── Configuration ─────────────────────────────────────────────────────────────

CWMS_BASE = "https://cwms-data.usace.army.mil/cwms-data"
ACCEPT    = "application/json;version=2"
OFFICE    = "LRL"

# Allowed difference between computed and reported Percent Util.
# The report rounds to 2 decimal places; the storage TS may lag the report
# time by up to one hour, so 0.15 is a reasonable tolerance.
TOLERANCE = 0.15

# Lake order matches the LRL Daily Lake Report
LAKES = [
    "CaesarCreek", "WHHarsha",  "WestFork",  "CJBrown",    "Brookville",
    "CaveRun",     "CarrCreek", "Buckhorn",  "Taylorsville","Green",
    "Nolin",       "Barren",    "Rough",     "CMHarden",    "CaglesMill",
    "Monroe",      "Patoka",
]

# Storage TS suffix — lrldlb-rev is in the catalog but returns no values
STOR_TS_SUFFIX = "lrldlb-comp"

# Reported Percent Util from LRL Daily Lake Report 2026-10-08
DEFAULT_REPORT_PERCENT_UTIL: dict[str, float] = {
    "CaesarCreek":   0.05,
    "WHHarsha":     -0.05,
    "WestFork":      0.24,
    "CJBrown":       0.79,
    "Brookville":    0.48,
    "CaveRun":       2.62,
    "CarrCreek":     7.20,
    "Buckhorn":      0.94,
    "Taylorsville": -0.68,
    "Green":        -0.85,
    "Nolin":         1.33,
    "Barren":        0.95,
    "Rough":        -0.18,
    "CMHarden":     -0.10,
    "CaglesMill":    0.18,
    "Monroe":       -0.37,
    "Patoka":       21.77,
}

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


def resolve_level(data: dict, query: datetime.datetime) -> tuple[float | None, str | None]:
    """Extract a scalar storage level from a CWMS location-level response."""
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


def fetch_percent_util(
    lake: str, win_begin: str, win_end: str, level_date: str, query_utc: datetime.datetime
) -> tuple[float | None, dict, str]:
    """Fetch storage data for *lake* and compute percent_util.

    Returns (percent_util, raw_responses_dict, notes_string).
    """
    raw: dict = {}

    # 1. Current storage timeseries
    ts_id  = f"{lake}.Stor.Inst.1Hour.0.{STOR_TS_SUFFIX}"
    ts_url = (f"{CWMS_BASE}/timeseries?name={urllib.parse.quote(ts_id, safe='')}"
              f"&office={OFFICE}&unit=ac-ft&begin={win_begin}&end={win_end}")
    ts_d = cwms_fetch(ts_url)
    raw["stor_ts_response"] = ts_d
    vals = ts_d.get("values", [])
    cur_stor: float | None = None
    if vals:
        best = min(vals, key=lambda v: abs(v[0] - int(query_utc.timestamp() * 1000)))
        if best[1] is not None:
            cur_stor = float(best[1])

    # 2. Storage at guide curve (Bottom of Flood Control)
    gc_url = (f"{CWMS_BASE}/levels/"
              f"{urllib.parse.quote(lake + '.Stor.Inst.0.Bottom of Flood Control', safe='')}"
              f"?office={OFFICE}&unit=ac-ft&effective-date={level_date}")
    gc_d = cwms_fetch(gc_url)
    raw["gc_level_response"] = gc_d
    gc_stor, gc_err = resolve_level(gc_d, query_utc)

    # 3. Storage at flood pool (Top of Flood)
    fl_url = (f"{CWMS_BASE}/levels/"
              f"{urllib.parse.quote(lake + '.Stor.Inst.0.Top of Flood', safe='')}"
              f"?office={OFFICE}&unit=ac-ft&effective-date={level_date}")
    fl_d = cwms_fetch(fl_url)
    raw["flood_level_response"] = fl_d
    fl_stor, fl_err = resolve_level(fl_d, query_utc)

    notes_parts = []
    if cur_stor is None:
        notes_parts.append(f"no stor (vals={len(vals)})")
    if gc_stor is None:
        notes_parts.append(f"gc_err={gc_err}")
    if fl_stor is None:
        notes_parts.append(f"fl_err={fl_err}")

    if cur_stor is not None and gc_stor is not None and fl_stor is not None:
        denom = fl_stor - gc_stor
        pct = round((cur_stor - gc_stor) / denom * 100, 2) if denom != 0 else None
    else:
        pct = None

    return pct, raw, "; ".join(notes_parts)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--date", default="2026-10-08",
        help="Report date YYYY-MM-DD (default: 2026-10-08)"
    )
    parser.add_argument(
        "--save-fixtures", action="store_true",
        help="Overwrite tests/fixtures/lrl_oct8_2026/cwms_responses.json with fetched data"
    )
    args = parser.parse_args()

    try:
        report_date = datetime.date.fromisoformat(args.date)
    except ValueError:
        print(f"ERROR: --date must be YYYY-MM-DD, got {args.date!r}", file=sys.stderr)
        return 1

    # 06:00 US/Eastern = 10:00 UTC in October (EDT = UTC-4)
    query_utc = datetime.datetime(
        report_date.year, report_date.month, report_date.day,
        10, 0, 0, tzinfo=datetime.timezone.utc
    )
    win_begin  = (query_utc - datetime.timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S")
    win_end    = (query_utc + datetime.timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S")
    level_date = query_utc.strftime("%Y-%m-%dT%H:%M:%S")

    report_pct = DEFAULT_REPORT_PERCENT_UTIL  # extend here for other dates

    print(f"\nLRL Percent Util validation — report date {args.date} (query UTC: {query_utc.isoformat()})")
    print(f"Storage window: {win_begin} → {win_end} UTC\n")
    print(f"{'Lake':<14} {'Report%':>8} {'Calc%':>8} {'Δ':>7} {'Pass':>5}  Notes")
    print("-" * 65)

    failures: list[str] = []
    all_raw: dict = {}

    for lake in LAKES:
        calc, raw, notes = fetch_percent_util(lake, win_begin, win_end, level_date, query_utc)
        all_raw[lake] = raw

        rpt = report_pct.get(lake)
        if rpt is None:
            print(f"{lake:<14} {'N/A':>8} {'N/A':>8} {'N/A':>7} {'?':>5}  no report value")
            continue

        if calc is None:
            print(f"{lake:<14} {rpt:>8.2f} {'N/A':>8} {'N/A':>7} {'FAIL':>5}  {notes}")
            failures.append(lake)
            continue

        diff = calc - rpt
        passed = abs(diff) <= TOLERANCE
        flag = "PASS" if passed else "FAIL"
        print(f"{lake:<14} {rpt:>8.2f} {calc:>8.2f} {diff:>+7.2f} {flag:>5}  {notes}")
        if not passed:
            failures.append(lake)

    print()
    if failures:
        print(f"FAILED ({len(failures)}/17 lakes outside ±{TOLERANCE}): {', '.join(failures)}")
    else:
        print(f"All 17 lakes within ±{TOLERANCE}  ✓")

    if args.save_fixtures and args.date == "2026-10-08":
        # Also fetch the elevation TS and guide curve elevation for the fixture
        print("\nRefreshing test fixtures …")
        for lake in LAKES:
            elev_ts_id = f"{lake}.Elev.Inst.0.0.lrldlb-rev"
            elev_url = (f"{CWMS_BASE}/timeseries?name={urllib.parse.quote(elev_ts_id,safe='')}"
                        f"&office={OFFICE}&unit=ft&begin={win_begin}&end={win_end}")
            all_raw[lake]["elev_ts_response"] = cwms_fetch(elev_url)

            gc_elev_url = (f"{CWMS_BASE}/levels/"
                           f"{urllib.parse.quote(lake+'.Elev.Inst.0.Bottom of Flood Control',safe='')}"
                           f"?office={OFFICE}&unit=ft&effective-date={level_date}")
            all_raw[lake]["elev_gc_level_response"] = cwms_fetch(gc_elev_url)

        fixture_path = (
            pathlib.Path(__file__).parent.parent
            / "tests" / "fixtures" / "lrl_oct8_2026" / "cwms_responses.json"
        )
        fixture_path.parent.mkdir(parents=True, exist_ok=True)
        with open(fixture_path, "w") as f:
            json.dump(all_raw, f, indent=2)
        print(f"Saved → {fixture_path}")
    elif args.save_fixtures:
        print("NOTE: --save-fixtures only writes the 2026-10-08 fixture (use --date 2026-10-08)")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
