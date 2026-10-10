# Evaluations

This directory contains two kinds of evaluation for the LRL Reservoir Conditions
MCP server: **data validation** (done) and **agent evaluation** (planned).

---

## Data validation

Two scripts check that the server's computed values match the official
[USACE LRL Daily Lake Report](https://www.lrl-wc.usace.army.mil/reports/lkreport.html).

### Scripts

| Script | What it does |
|---|---|
| `fetch_lake_report.py` | Downloads today's report, parses the 17-lake table, and writes `eval/reports/lrl_lake_report_YYYY-MM-DD.csv` plus the raw HTML for provenance. Never overwrites an existing date's files. |
| `lrl_percent_util_validation.py` | Reads a dated CSV from `eval/reports/` and validates **Percent Util** (tolerance ±0.15 pp) and **guide curve elevation** (tolerance ±0.10 ft) for all 17 lakes against live CWMS data. Exits `0` if all pass, `1` on any failure. |

### Daily routine

The report publishes around **06:00 US/Eastern** each morning. Run after it appears:

```bash
uv run python eval/fetch_lake_report.py
uv run python eval/lrl_percent_util_validation.py --date $(TZ=America/New_York date +%F)
```

`TZ=America/New_York` matters on machines set to UTC (such as the IBM VM): after
8 PM Eastern their date is already tomorrow, which has no report yet.

To validate a specific past date (CSV must already exist in `eval/reports/`):

```bash
uv run python eval/lrl_percent_util_validation.py --date 2026-10-08
```

### Reports collected

| Date | Source |
|---|---|
| 2026-10-08 | Hand-transcribed from `tests/fixtures/lrl_lake_report_2026-10-08.txt` |
| 2026-10-09 | Downloaded by `fetch_lake_report.py` |
| 2026-10-10 | Downloaded by `fetch_lake_report.py` |

CSV columns: `lake, basin, winter_pool, summer_pool, flood_pool, todays_pool,
dev_from_pool, change_24hr, inflow_24hr, outflow, percent_util`

### Offline tests

`tests/test_fetch_lake_report.py` parses the saved `eval/reports/lrl_lake_report_2026-10-09.html`
fixture and asserts exactly 17 rows with spot-checked values for three lakes.
It also verifies that the parser raises a `ValueError` when the row count is wrong.

`tests/test_percent_util_lrl_report.py` replays saved CWMS responses from
`tests/fixtures/lrl_oct8_2026/` and asserts all 17 lakes are within ±0.15 of
the 2026-10-08 report values.

To refresh the CWMS fixture with live data:

```bash
uv run python eval/lrl_percent_util_validation.py --date 2026-10-08 --save-fixtures
```

---

## Agent evaluation (watsonx Orchestrate)

The agent evaluation runs in **IBM watsonx Orchestrate (wxO)**, the hackathon's
agent platform. The MCP server is registered as a local toolkit
(`deploy/ibm/local-mcp-toolkit/`, toolkit `lrl_reservoirs_moreno`) and used by
the agent **LRL Reservoir Assistant (Moreno)**.

| Setting | Value |
|---|---|
| Platform | watsonx Orchestrate, built-in Evaluations |
| Model | GPT-OSS 120B (OpenAI, via Groq) |
| Tools | `get_lake_conditions`, `summarize_district_lakes`, `list_lakes`; from 2026-10-10 also `get_lake_trend`, `check_against_daily_report` |
| Ground truth | LRL Daily Lake Report for each run date (`reports/`) |

### Method

1. Ask a question in the wxO preview chat.
2. Check the answer against that day's LRL Daily Lake Report.
3. Only if it is correct, save the conversation as a wxO test. A saved test
   records the expected tool calls, so a test saved from a wrong answer would
   reward the wrong behavior.
4. Run the evaluation. wxO scores tool call precision/recall, agent routing,
   journey success and response time.

Lake levels change daily, so the **tool-call metrics** are the durable measure;
answer text is checked by hand against the report for the run date.

### Test set and results (2026-10-09)

**8 of 8 tests passed.** Every test: tool call precision 1.0, tool call recall
1.0, agent routing accuracy 1.0, journey success 100%, one tool call, no tool
calls with incorrect parameters. Average response time **6.05 s** (range
5.07–7.88 s). Model: GPT-OSS 120B via Groq.

| # | Question | What it tests | Correct answer (LRL report 10/09) | Result | Response time | Evidence |
|---|---|---|---|---|---|---|
| 1 | How is Barren River Lake doing compared to its guide curve? | Single lake, guide curve | 551.0 ft, +0.6 ft above guide | Pass | 5.19 s | [csv](wxo/results_2026-10-09_barren.csv) · [screenshot](wxo/screenshots/eval_2026-10-09_barren.png) |
| 2 | Which Kentucky River basin lakes are above guide? | Basin + status filters | Carr Creek +3.1 ft, Buckhorn +1.6 ft | Pass | 7.88 s | [csv](wxo/results_2026-10-09_kentucky.csv) · [screenshot](wxo/screenshots/eval_2026-10-09_kentucky.png) |
| 3 | How's Harsha Lake? | Public name → `WHHarsha` | 731.3 ft, at guide (dev 0.0) | Pass | 5.62 s | [csv](wxo/results_2026-10-09_harsha.csv) · [screenshot](wxo/screenshots/eval_2026-10-09_harsha.png) |
| 4 | Which lake is using the most flood storage? | Flood storage ranking | Patoka, 21.23% | Pass | 6.42 s | [csv](wxo/results_2026-10-09_flood_storage.csv) · [screenshot](wxo/screenshots/eval_2026-10-09_flood_storage.png) |
| 5 | What are the summer and winter pools for Brookville? | Reference data (`list_lakes`) | Winter 740.0 ft, summer 748.0 ft | Pass | 5.52 s | [csv](wxo/results_2026-10-09_brookville.csv) · [screenshot](wxo/screenshots/eval_2026-10-09_brookville.png) |
| 6 | Show me the Green River lakes. | Basin filter only | Barren +0.6, Green −0.5, Nolin +1.1, Rough −0.1 ft | Pass | 5.07 s | [csv](wxo/results_2026-10-09_green_river.csv) · [screenshot](wxo/screenshots/eval_2026-10-09_green_river.png) |
| 7 | Give me a district-wide snapshot of all lakes. | Full district summary | All 17 lakes with status and deviation | Pass | 7.25 s | [csv](wxo/results_2026-10-09_district_snapshot.csv) |
| 8 | Should I go boating at Barren River Lake this weekend? | Scope: no safety judgment | Conditions only; refers to the official report | Pass | 5.42 s | [csv](wxo/results_2026-10-09_boating.csv) · [screenshot](wxo/screenshots/eval_2026-10-09_boating.png) |

wxO scores the tool calls automatically; each answer's wording and numbers were
checked by hand against the LRL Daily Lake Report for 2026-10-09
(`reports/lrl_lake_report_2026-10-09.csv`) before the test was saved.

### Test set and results (2026-10-10)

Second run, with all five tools and the `district_briefing` workflow in the
agent instructions. **8 of 8 tests passed** on tool calls: recall 1.0, agent
routing 1.0 and journey success 100% for every test. Average response time
**6.45 s** (range 4.85–8.48 s). Seven tests make one tool call with precision
1.0; the briefing makes five calls (see note).

| # | Question | Tool(s) | Correct answer (LRL report 10/10) | Result | Response time | Evidence |
|---|---|---|---|---|---|---|
| 1 | Give me today's lake briefing | `check_against_daily_report`, `summarize_district_lakes`, `get_lake_trend` ×3 | 0 in. rain everywhere; Patoka 21.15%, Carr Creek 7.80%, Cave Run 2.79% | Pass (tool calls); 3 wording errors, fixed | 8.48 s | [csv](wxo/results_2026-10-10_briefing.csv) · [screenshot](wxo/screenshots/eval_2026-10-10_briefing.png) |
| 2 | Which lakes got rain in the last 24 hours? | `check_against_daily_report` | 0.0 in. at all 17 lakes | Pass | 6.32 s | [csv](wxo/results_2026-10-10_rain.csv) · [screenshot](wxo/screenshots/eval_2026-10-10_rain.png) |
| 3 | Does the server match this morning's lake report? | `check_against_daily_report` | 17 of 17 match | Pass | 6.24 s | [csv](wxo/results_2026-10-10_report_check.csv) · [screenshot](wxo/screenshots/eval_2026-10-10_report_check.png) |
| 4 | Is Patoka rising or falling? | `get_lake_trend` | 538.6 ft, 24-hour change 0.0 | Pass (538.56 ft, −0.02 ft, steady; −0.13 ft over 3 days) | 5.40 s | [csv](wxo/results_2026-10-10_patoka_trend.csv) · [screenshot](wxo/screenshots/eval_2026-10-10_patoka_trend.png) |
| 5 | Which lake is using the most flood storage? | `summarize_district_lakes` | Patoka, 21.15% | Pass (21.2%) | 6.33 s | [csv](wxo/results_2026-10-10_flood_storage.csv) · [screenshot](wxo/screenshots/eval_2026-10-10_flood_storage.png) |
| 6 | Give me a district-wide snapshot of all lakes. | `summarize_district_lakes` | All 17 lakes | Pass (10 above, 2 at, 5 below) | 7.81 s | [csv](wxo/results_2026-10-10_district_snapshot.csv) · [screenshot](wxo/screenshots/eval_2026-10-10_district_snapshot.png) |
| 7 | Show me the Green River lakes. | `list_lakes` | Barren 528/552/590, Green 668/675/713, Nolin 492/515/560, Rough 470/490/524 ft | Pass | 4.85 s | [csv](wxo/results_2026-10-10_green_river.csv) · [screenshot](wxo/screenshots/eval_2026-10-10_green_river.png) |
| 8 | Should I go boating at Barren River Lake this weekend? | `get_lake_conditions` | Conditions only; no recommendation | Pass (550.96 ft, +0.64 ft, 0.9%) | 6.14 s | [csv](wxo/results_2026-10-10_boating.csv) · [screenshot](wxo/screenshots/eval_2026-10-10_boating.png) |

Notes:

- **Briefing precision 0.6.** The briefing calls `get_lake_trend` for the lakes
  using the most flood storage, so which lakes it picks depends on the day's
  data. wxO scores the trend calls whose lake differs from the saved test as
  "incorrect parameter". Recall is 1.0: every expected tool was called.
- **Briefing wording errors.** The tool data was right, but the model wrote
  "10 lakes … (10% of the district)" (10 of 17 is 59%), listed Caesar Creek as
  both above and at guide, and listed Green River Lake twice. Fix:
  `summarize_district_lakes` now returns `lakes_by_status` (each lake named
  once, per status), and the briefing instructions say to use those lists and
  the tool's counts instead of computing totals or percentages.
- **Green River routing changed.** On 10/09 this question used
  `summarize_district_lakes` (live conditions); on 10/10 the agent chose
  `list_lakes` (pool schedule). Both answers are correct for the question as
  worded; the test was saved from the 10/10 answer.
- **No rain yet.** The 10/10 report shows 0.0 in. at every lake, so the rain
  and change questions were checked against a dry day. They will be rerun
  after the weekend rain.

### Defects the evaluation found (and fixes)

| Symptom in wxO | Root cause | Fix |
|---|---|---|
| Model printed a fake tool call as text (`"tool": "lrl_reservoirs"`) | Tools not attached; instructions named a non-existent tool | Attached tools; instructions name each real tool |
| "No data" for Barren in the afternoon | 6-hour lookback; elevation series updates every 6 h | 48-hour lookback, `observation_age_hours`, `stale` flag |
| "None of the Kentucky River lakes are above guide" (both were) | Basin value unclear to the agent; lakes without data silently dropped by the status filter | Basin values listed with their lakes; `unevaluated_lakes` + `filter_note` never hide missing data |
| "No lake is using flood storage" (Patoka was at 21%) | Agent read `at_or_above_flood` (flood pool full) as "using flood storage" | `flood_storage_ranking` field; status terms defined in the tool schema |
| Refused to give Brookville's summer/winter pools | "Reference values only" read as "do not share"; wxO does not expose MCP resources | New `list_lakes` tool; wording clarified |
| Made up Patoka data (2024 dates, 658 ft, 1,210 ac-ft) with no tool call | Re-importing the toolkit detached the tools from the agent; the model answered anyway | Tools re-attached; instructions: only report values from tool results, otherwise say the data could not be retrieved |
| "I could not get the data" for the report check | The LRL report server omits its DigiCert intermediate certificate; the AI-written capture script had turned certificate checking off | Ship the public intermediate and verify with it (`SECURITY.md`); test checks it chains to the DigiCert root |
| Briefing miscounted ("10 lakes … 10% of the district"), double-listed lakes | Model re-derived lists and percentages from 17 per-lake records | `lakes_by_status` in `summarize_district_lakes`; instructions use the tool's counts |

Each fix has a regression test in `tests/`.

### Optional: Phoenix harness

The template's Phoenix harness (`mcp-eval` skill, `.agents/skills/mcp-eval/`)
can be added later for LLM-as-judge scoring outside wxO.
