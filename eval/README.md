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

## Agent evaluation (planned)

The next step is a Phoenix-based evaluation harness that measures how well an
LLM agent can answer realistic lake-conditions questions using only the MCP
server's tools.

Use the **`mcp-eval` skill** (`.agents/skills/mcp-eval/`) to scaffold it.
The harness will live in `eval/phoenix/` and follow this layout:

```
eval/phoenix/
├── agent.py            # LRLAgent — launches the server over stdio
├── create_dataset.py   # CLI: upload a CSV dataset to Phoenix
├── run_experiment.py   # CLI: run agent + judges against a dataset
├── datasets.yaml       # Dataset registry
├── judges/             # LLM-as-judge evaluators (correctness, scope)
├── prompts/            # Versioned agent system prompts
└── datasets/<name>/    # evaluation.xml (source) + examples/<name>.csv
```

The companion **`mcp-builder` skill** (`.agents/skills/mcp-builder/`) covers
writing the 10 gold questions (`evaluation.xml`) that become the harness's
dataset.

Add eval-only dependencies to the `dev` group in `pyproject.toml` when you
scaffold the harness (the `mcp-eval` skill lists the exact versions), then
`uv sync --group dev`.
