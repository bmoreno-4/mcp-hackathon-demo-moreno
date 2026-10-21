# Evaluations

This directory contains two kinds of evaluation for the LRL Reservoir Conditions
MCP server: **data validation** (the server's numbers vs. the official report,
daily since 2026-10-08) and **agent evaluation** (a watsonx Orchestrate agent's
answers vs. the official report, 2026-10-09 to 2026-10-15).

---

## Data validation

Two scripts check that the server's computed values match the official
[USACE LRL Daily Lake Report](https://www.lrl-wc.usace.army.mil/reports/lkreport.html).

### Scripts

| Script | What it does |
|---|---|
| `fetch_lake_report.py` | Downloads today's report, parses the 17-lake table, and writes `eval/reports/lrl_lake_report_YYYY-MM-DD.csv` plus the raw HTML for provenance. Never overwrites a saved report; a later same-day revision is saved as `_rev-HHMM`. |
| `lrl_percent_util_validation.py` | Reads a dated CSV from `eval/reports/` and validates **Percent Util** (tolerance ±0.15 pp) and **guide curve elevation** (tolerance ±0.10 ft) for all 17 lakes against live CWMS data. Exits `0` if all pass, `1` on any failure. |

### Daily routine

The report publishes around **06:00 US/Eastern** each morning. Run after it appears:

```bash
uv run python eval/fetch_lake_report.py
uv run python eval/lrl_percent_util_validation.py --date $(TZ=America/New_York date +%F)
```

Run `fetch_lake_report.py` again mid-morning (around 09:00). If USACE has
revised the report, the new version is saved as `_rev-HHMM`; if not, nothing
is written.

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
| 2026-10-11 | Downloaded by `fetch_lake_report.py` (first day with rain); revised by USACE after 07:53 |
| 2026-10-12 | Downloaded by `fetch_lake_report.py` (first 24-hour rise: Carr Creek +0.3 ft) |
| 2026-10-13 | Downloaded by `fetch_lake_report.py` |
| 2026-10-14 | Downloaded by `fetch_lake_report.py` |
| 2026-10-15 | Downloaded by `fetch_lake_report.py` (second rain event: 16 lakes) |
| 2026-10-21 | Downloaded by `fetch_lake_report.py` (10/16 to 10/20 not captured) |

CSV columns: `lake, basin, winter_pool, summer_pool, flood_pool, todays_pool,
dev_from_pool, change_24hr, inflow_24hr, outflow, percent_util`

### Validation results

| Report date | Percent Util (±0.15) | Guide curve (±0.10 ft) | Largest differences |
|---|---|---|---|
| 2026-10-08 | 17 of 17; 15 exact | 17 of 17 | −0.08 Barren, Carr Creek |
| 2026-10-09 | 17 of 17 | 17 of 17 | not recorded |
| 2026-10-10 | 17 of 17; 14 exact | 17 of 17 | −0.09 Barren, −0.08 Carr Creek; guide curve +0.10 ft C. M. Harden |
| 2026-10-11 (rain) | 17 of 17; 13 exact | 17 of 17 | −0.11 Barren, −0.09 Carr Creek; guide curve +0.10 ft C. M. Harden |
| 2026-10-12 | 17 of 17; 13 exact | 17 of 17 | −0.11 Barren, −0.10 Carr Creek; guide curve +0.09 ft Harsha, Buckhorn |
| 2026-10-13 | 17 of 17; 12 exact | 17 of 17 | −0.11 Barren, −0.11 Carr Creek; guide curve −0.08 ft Nolin |
| 2026-10-14 | 17 of 17; 14 exact | **16 of 17 (Harsha failed)** | Harsha guide curve just over +0.10 ft; −0.11 Barren, −0.10 Carr Creek |
| 2026-10-15 (rain) | 17 of 17; 13 exact | 17 of 17 | **−0.14 Barren** (limit 0.15), −0.10 Carr Creek; guide curve −0.08 ft C. J. Brown |
| 2026-10-21 | **16 of 17 (Barren failed)**: Barren −0.38; 14 exact | 17 of 17 | **−0.38 Barren** (limit 0.15), −0.09 Carr Creek; guide curve +0.09 ft C. J. Brown |

**2026-10-14 failure (Harsha guide curve).** The report shows Harsha's pool as
731.1 ft and its deviation from guide as 0.1 ft, so the guide curve derived
from the report is 731.0 ft; the server's live guide curve was about 731.10 ft.
The script compares the difference rounded to four decimals, and it was just
over the 0.10 ft limit (displayed as +0.10, so less than 0.005 ft over). Both
report values are rounded to 0.1 ft, so the derived guide curve can be off by
up to just under 0.10 ft from rounding alone; the tolerance sits exactly at that
bound with no margin, and a tiny difference in when the falling October guide
curve is computed is enough to cross it. C. M. Harden passed at +0.10 on 10/10
and 10/11 for the same reason, just under the line. The tolerance was not
changed after the failure; the result stands as recorded.

**2026-10-21 failure (Barren Percent Util).** Reports for 10/16 to 10/20 were
not captured. On 10/21 the report showed Barren at 4.27% and the server
computed 3.89%, a −0.38 difference against a ±0.15 limit. Barren's offset had
been −0.08 to −0.14 since 10/08 and grew after the 10/15 rain, while its guide
curve was falling for the fall drawdown (550.5 ft on 10/08, 549.9 on 10/15,
547.1 on 10/21). The guide curve elevation itself still matched (−0.01 ft), so
the difference is in a storage value: either the storage the report uses for
Barren's guide curve or its current storage. Nolin and Cave Run had similar
drawdowns with no offset, so drawdown alone does not explain it; the cause is
not confirmed. The result is recorded as a failure, and the tolerance was not
changed.

**Consistent offsets.** Barren (−0.08 to −0.14) and Carr Creek (−0.08 to
−0.11) are the only lakes with a repeated Percent Util difference. Carr
Creek's stays near 1% of its Percent Util as that rises (7.20 → 9.60), which
suggests a slightly different flood-pool storage value. Barren's held near
−0.11 until 10/15, when 1.7 in. of rain (inflow 341 cfs) took it to −0.14,
0.01 from the limit. Both are within tolerance and documented rather than tuned
away; Barren is the most likely next failure.

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

### First rain day (2026-10-11)

The 10/11 report shows rain at 13 of 17 lakes (Barren 1.0 in., Cave Run and
Green 0.6 in., Carr Creek and Buckhorn 0.5 in.) and higher inflows (Barren 432
cfs, up from 24). The district snapshot test passed again (tool call
precision and recall 1.0, 8.35 s;
[csv](wxo/results_2026-10-11_district_snapshot.csv)).

The briefing called the right tools
([trace](wxo/screenshots/eval_2026-10-11_briefing_trace.png)) and, with
`lakes_by_status`, now listed every lake once (11 above, 1 at, 5 below guide).
But it said **no lake recorded precipitation**, and that the 11 lakes above
guide "total ≈21% of the district's flood storage" (21% is Patoka alone)
([screenshot](wxo/screenshots/defect_2026-10-11_briefing_missed_rain.png)).
It was not saved as a test. Fix: `check_against_daily_report` now returns
`rain_last_24hr` (every lake with rain, wettest first) and
`changes_since_report` (only the values that moved), and the instructions say
Percent Util is per lake and never summed. Regression tests use the saved
10/11 report.

**Rerun after the fix (08:16–08:19):**

| Question | Tool(s) | Checked against | Result | Time | Evidence |
|---|---|---|---|---|---|
| Which lakes got rain in the last 24 hours? | `check_against_daily_report` | Report as revised by 08:24 | Pass: all 13 lakes and amounts, wettest first | 6.31 s | [csv](wxo/results_2026-10-11_rain.csv) · [screenshot](wxo/screenshots/eval_2026-10-11_rain.png) |
| Give me today's lake briefing | report check, summary, `get_lake_trend` ×3 | Same | Pass: rain listed, Percent Util per lake, each lake once; precision 0.6 (data-dependent trend lakes) | 7.10 s | [csv](wxo/results_2026-10-11_briefing.csv) · [screenshot](wxo/screenshots/eval_2026-10-11_briefing.png) |
| Give me a district-wide snapshot of all lakes. | `summarize_district_lakes` | Same | Pass: no rain claims without the report tool | 6.95 s | [csv](wxo/results_2026-10-11_district_snapshot_v2.csv) · [screenshot](wxo/screenshots/eval_2026-10-11_district_snapshot.png) |

**The official report changed after it was captured.** The copy saved at
07:53 showed Nolin 0.2 in., Green 0.6 in. and Rough 0.1 in. of rain; by 08:17
the live page showed 1.0, 0.4 and 0.3 (Nolin inflow 189 → 219 cfs). The agent
reported the revised values, which matched the live page when checked by hand
at 08:24. The "answer key" is therefore not fixed at 06:00:
`fetch_lake_report.py` now saves a later download as
`lrl_lake_report_<date>_rev-HHMM` when its HTML differs from what is already
saved, and never overwrites the original. Re-run it mid-morning.

### Day after the rain (2026-10-12)

First report with a real 24-hour rise: Carr Creek +0.3 ft (inflow 19 → 117
cfs, 8.23% → 9.35% Percent Util), Buckhorn +0.1 ft; rain at 7 lakes. Both
answers were correct against the report with no fixes needed.

| Question | Tool(s) | Correct answer (LRL report 10/12) | Result | Time | Evidence |
|---|---|---|---|---|---|
| Which lakes got rain in the last 24 hours? | `check_against_daily_report` | Barren 0.7, Buckhorn 0.6, Carr Creek 0.6, Nolin 0.3, Cave Run 0.2, Green 0.1, Harsha 0.1 in. | Pass: all 7, wettest first | 6.89 s | [csv](wxo/results_2026-10-12_rain.csv) · [screenshot](wxo/screenshots/eval_2026-10-12_rain.png) |
| Give me today's lake briefing. | report check, summary, `get_lake_trend` ×3 | Rain as above; Carr Creek +0.3 ft in 24 h; Patoka 21.22%, Carr Creek 9.35%, Cave Run 3.12% | Pass: each lake once (10 above, 2 at, 5 below), Percent Util per lake, Carr Creek "rising, +0.3 ft in 24 h, peak at latest reading"; precision 0.6 (data-dependent trend lakes) | 7.59 s | [csv](wxo/results_2026-10-12_briefing.csv) · [screenshot](wxo/screenshots/eval_2026-10-12_briefing.png) |

### Briefing reruns (2026-10-13 to 2026-10-15)

The saved briefing test was rerun at 18:06 ET against the 10/13 report (rain at
3 lakes; Carr Creek 9.64%, now +4.0 ft above guide).

| Question | Correct answer (LRL report 10/13) | Result | Time | Evidence |
|---|---|---|---|---|
| Give me today's lake briefing. | Rain: Taylorsville 0.4, Harsha 0.3, Cave Run 0.2 in.; Patoka 21.14%, Carr Creek 9.64% | Pass: rain exact; each lake once (10 above, 2 at, 5 below); Patoka 21.1%, Carr Creek 9.5% (within tolerance); Carr Creek rising +0.61 ft over 3 days; recall 1.0, precision 0.6 | 8.01 s | [csv](wxo/results_2026-10-13_briefing.csv) · [screenshot](wxo/screenshots/eval_2026-10-13_briefing.png) |

The briefing was correct on 10/13, 10/14 and 10/15 with no changes to the
server or the instructions.

| Date | Report conditions | Briefing result | Recall / precision | Time | Evidence |
|---|---|---|---|---|---|
| 10/14 | No rain; Carr Creek −0.2 ft in 24 h | Correct: no rain; Carr Creek falling (−0.18 ft); Patoka 21.1% (report 21.06), Carr Creek 9.4% (9.47), Cave Run 3.4% (3.41); 9 above, 3 at, 5 below guide | 1.0 / 0.6 | 8.28 s | [csv](wxo/results_2026-10-14_briefing.csv), [screenshot](wxo/screenshots/eval_2026-10-14_briefing.png) |
| 10/15 | Rain at 16 lakes (Barren 1.7 in.); West Fork +0.7 ft | Correct: all 16 rain amounts; 11 above, 2 at, 4 below; Patoka 21.4% (21.43); Buckhorn Percent Util up since 6 AM (1.56 to 1.8); West Fork chosen for a trend because of its +0.67 ft rise | 1.0 / 0.5 | 7.88 s | [csv](wxo/results_2026-10-15_briefing.csv), [screenshot](wxo/screenshots/eval_2026-10-15_briefing_and_followup.png) |

On 10/14 the data validation failed one check (Harsha's guide curve, see
[Validation results](#validation-results)). That does not affect the briefing:
it listed Harsha at guide, the server's live deviation was within ±0.05 ft, and
the report showed +0.1 ft, a difference within the report's 0.1 ft rounding.

On 10/15 a follow-up question in the same chat, "Can you give me more detail on
Cave Run Lake's 3-day elevation trend?", was the first multi-turn test. The
agent called `get_lake_trend` and listed all 11 readings (729.53 to 729.58 ft),
steady over 24 hours and over 3 days, with Percent Util rising from 3.2% to
3.7% (report 3.66%).

### Plain-language briefing (2026-10-21)

The agent instructions gained a "Writing for the public" section (plain terms,
Eastern times, technical detail only on request). The saved briefing test
passed on tool calls (recall 1.0, precision 0.6, 8.31 s), and the answer used a
plain summary, "2.6 ft above its target" and whole percentages. It also called
the day **"Friday, October 21"**; October 21, 2026 was a Wednesday. It still
used "CWMS" and "guide curve" and gave no Eastern reading times. The answer was
not saved as a public-briefing test
([csv](wxo/results_2026-10-21_briefing_public.csv),
[screenshot](wxo/screenshots/eval_2026-10-21_briefing_public.png)).

**Rerun at 19:36, after adding Eastern date and time labels and each lake's
USACE Water Data page.** The first attempt after re-registering returned "unable
to retrieve the data": the agent's tool cards still pointed at the removed copy
of the toolkit. Removing and re-adding the five tools fixed it. The rerun
passed on tool calls (recall 1.0, precision 0.6, 8.40 s) and checked against
the 10/21 report:

- Correct: "Wednesday, October 21 2026 (6 AM EDT)"; no rain; 11 above, 1 at,
  5 below target; Patoka 2.6 ft above target, 19% (report 18.91), Carr Creek
  3.9 ft, 9% (8.87), Cave Run 2.8 ft, 5% (5.38).
- Wrong: "all other lakes above target are using between 1% and 8%" (they were
  0.13% to 4.4%), and the one change since 6 AM, Barren's Percent Util (4.27
  to about 3.9, the same gap that failed validation), was attributed to Patoka.
- Mixed formats: it wrote the plain summary and then the technical sections,
  with "guide curve" and "CWMS" still in them.

Fix: the public version now has its own fixed sections ("using only these
sections"), one line per lake from `flood_storage_ranking`, a rule never to
summarize several lakes as a range, and a rule to name each changed lake
exactly as given in `changes_since_report`. Not saved as a test
([csv](wxo/results_2026-10-21_briefing_public_rerun.csv),
[screenshot](wxo/screenshots/eval_2026-10-21_briefing_public_rerun.png)).

### Defects the evaluation found (and fixes)

| Symptom in wxO | Root cause | Fix |
|---|---|---|
| Model printed a fake tool call as text (`"tool": "lrl_reservoirs"`) | Tools not attached; instructions named a non-existent tool | Attached tools; instructions name each real tool |
| "No data" for Barren in the afternoon | 6-hour lookback; elevation series updates every 6 h | 48-hour lookback, `observation_age_hours`, `stale` flag |
| "None of the Kentucky River lakes are above guide" (both were) | Basin value unclear to the agent; lakes without data silently dropped by the status filter | Basin values listed with their lakes; `unevaluated_lakes` + `filter_note` never hide missing data |
| "No lake is using flood storage" (Patoka was at 21%) | Agent read `at_or_above_flood` (flood pool full) as "using flood storage" | `flood_storage_ranking` field; status terms defined in the tool schema |
| Refused to give Brookville's summer/winter pools | "Reference values only" read as "do not share"; wxO does not expose MCP resources | New `list_lakes` tool; wording clarified |
| Made up Patoka data (2024 dates, 658 ft, 1,210 ac-ft) with no tool call | Re-importing the toolkit detached the tools from the agent; the model answered anyway | Tools re-attached; instructions: only report values from tool results, otherwise say the data could not be retrieved |
| "I could not get the data" for the report check | The LRL report server omits its DigiCert intermediate certificate; the SI/AI-written capture script had turned certificate checking off | Ship the public intermediate and verify with it (`SECURITY.md`); test checks it chains to the DigiCert root |
| Briefing miscounted ("10 lakes … 10% of the district"), double-listed lakes | Model re-derived lists and percentages from 17 per-lake records | `lakes_by_status` in `summarize_district_lakes`; instructions use the tool's counts |
| Briefing said "no precipitation" on the first rain day (13 lakes had rain) and summed Percent Util across lakes | Rain was only inside 17 nested lake records; nothing said Percent Util is per lake | `rain_last_24hr` and `changes_since_report` lists in `check_against_daily_report`; per-lake rule in instructions |
| Plain-language briefing called Wednesday, October 21 "Friday" | The model worked out the weekday itself; tools return only UTC timestamps | Tools now return ready-made Eastern labels (`report_date_label`, `report_time_eastern`, `as_of_eastern`, `time_eastern`), with tests including the 11/1 change to standard time; the instructions say to use them as given. Confirmed in the 10/21 rerun ("Wednesday", "6 AM EDT") |
| Public briefing invented a "1% to 8%" range and put Barren's change under Patoka | Model summarized and re-labeled values instead of copying them per lake; public and technical formats both in the instructions | Public version has its own sections; one line per lake; no ranges; changed lakes named exactly as given. To be confirmed in the next evaluation |

Fixes to the server code have regression tests in `tests/`. The two fixes made
only in the agent instructions (the fake tool call and the made-up Patoka
answer) cannot be unit-tested; a test checks that the instructions name every
registered tool, and the evaluation reruns showed the behavior did not return.

### Optional: Phoenix harness

The template's Phoenix harness (`mcp-eval` skill, `.agents/skills/mcp-eval/`)
can be added later for LLM-as-judge scoring outside wxO.
