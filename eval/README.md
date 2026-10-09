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
uv run python eval/lrl_percent_util_validation.py --date $(date +%F)
```

To validate a specific past date (CSV must already exist in `eval/reports/`):

```bash
uv run python eval/lrl_percent_util_validation.py --date 2026-10-08
```

### Reports collected

| Date | Source |
|---|---|
| 2026-10-08 | Hand-transcribed from `tests/fixtures/lrl_lake_report_2026-10-08.txt` |
| 2026-10-09 | Downloaded by `fetch_lake_report.py` |

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
| Tools | `get_lake_conditions`, `summarize_district_lakes`, `list_lakes` |
| Ground truth | LRL Daily Lake Report, 2026-10-09 (`reports/`) |

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

### Defects the evaluation found (and fixes)

| Symptom in wxO | Root cause | Fix |
|---|---|---|
| Model printed a fake tool call as text (`"tool": "lrl_reservoirs"`) | Tools not attached; instructions named a non-existent tool | Attached tools; instructions name each real tool |
| "No data" for Barren in the afternoon | 6-hour lookback; elevation series updates every 6 h | 48-hour lookback, `observation_age_hours`, `stale` flag |
| "None of the Kentucky River lakes are above guide" (both were) | Basin value unclear to the agent; lakes without data silently dropped by the status filter | Basin values listed with their lakes; `unevaluated_lakes` + `filter_note` never hide missing data |
| "No lake is using flood storage" (Patoka was at 21%) | Agent read `at_or_above_flood` (flood pool full) as "using flood storage" | `flood_storage_ranking` field; status terms defined in the tool schema |
| Refused to give Brookville's summer/winter pools | "Reference values only" read as "do not share"; wxO does not expose MCP resources | New `list_lakes` tool; wording clarified |

Each fix has a regression test in `tests/`.

### Optional: Phoenix harness

The template's Phoenix harness (`mcp-eval` skill, `.agents/skills/mcp-eval/`)
can be added later for LLM-as-judge scoring outside wxO.
