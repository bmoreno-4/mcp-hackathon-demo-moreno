# LRL Reservoir Conditions — MCP Server

An [MCP](https://modelcontextprotocol.io) server that gives AI clients live pool
conditions for all 17 **USACE Louisville District (LRL)** flood-risk management
reservoirs, using real-time data from the Corps of Engineers CWMS Data API.

Built with [FastMCP](https://github.com/jlowin/fastmcp) and [uv](https://docs.astral.sh/uv/).

---

## What it does

The LRL district operates 17 reservoirs across Kentucky, Ohio, and Indiana for
flood risk management. Each day the district publishes a **Daily Lake Report**
showing pool elevation, deviation from the seasonal guide curve, storage
utilisation (Percent Util), and inflow/outflow for every lake.

This server exposes five MCP tools. Four read live data; one is static
reference data:

| Tool | Description |
|---|---|
| `get_lake_conditions` | Current conditions for a single lake by CWMS location ID |
| `summarize_district_lakes` | Conditions for all 17 lakes in parallel, with aggregate counts |
| `list_lakes` | Lake directory and pool schedule (winter, summer, flood pool); no network call |
| `get_lake_trend` | One lake over the last 1-14 days: change, 24-hour change, rising/falling/steady, toward or away from the guide curve, peak and low |
| `check_against_daily_report` | Fetches today's LRL Daily Lake Report and compares it lake by lake with live CWMS values; adds the report's 24-hour precipitation, change, inflow and outflow |

Inputs are validated before any network call (fixed lake, basin and status
lists; `days` 1-14). Every tool's output is a Pydantic model in
[`models.py`](src/lrl_reservoirs/models.py), published to clients as the tool's
MCP output schema, so an agent knows each field, its type and its allowed values
before calling. `tests/test_models.py` checks every tool's real output against
its model.

It also provides one MCP prompt, `district_briefing` (optional basin), which
runs a daily briefing: it checks this morning's report, summarizes the
district, and pulls 3-day trends for the lakes using the most flood storage.
watsonx Orchestrate does not show MCP prompts to agents, so the same workflow
is in [`deploy/ibm/local-mcp-toolkit/agent_instructions.md`](deploy/ibm/local-mcp-toolkit/agent_instructions.md)
for pasting into the agent; a test keeps it in step with the registered tools.

### Example questions an AI client can answer

- *"How is Barren River Lake doing compared to its guide curve?"*
- *"Which lakes are currently below their guide curve?"*
- *"Show me a district summary - how many lakes are above guide?"*
- *"What is the Percent Util for Patoka Lake right now?"*
- *"Compare the storage utilization for all Green River basin lakes."*
- *"What has changed since this morning's lake report?"*
- *"Which lakes got rain in the last 24 hours?"*
- *"Is Patoka rising or falling, and is it getting closer to its guide curve?"*
- *"Give me today's lake briefing for the Green River basin."*

### Fields returned per lake

| Field | Description |
|---|---|
| `elevation_ft` | Current pool elevation in feet (NGVD-29) |
| `guide_curve_ft` | Today's seasonal Bottom of Flood Control elevation |
| `deviation_from_guide_curve_ft` | Elevation minus guide curve; negative = below guide |
| `pool_status` | `below_guide` / `at_guide` / `above_guide` / `at_or_above_flood` / `no_data` |
| `percent_to_flood_pool` | Elevation-based position through flood-control buffer (0 % = at guide, 100 % = flood pool) |
| `storage_acre_ft` | Current pool storage (acre-feet) |
| `storage_at_guide_curve_acre_ft` | Storage in acre-feet at today's guide curve elevation |
| `storage_at_flood_pool_acre_ft` | Storage in acre-feet at top of flood pool |
| `percent_util` | Storage-based utilisation — matches the USACE Daily Lake Report **Percent Util** column |
| `reference_levels` | Static pool schedule: winter/summer/flood pool elevations |

---

## Repo structure

```
lrl-reservoirs/
├── README.md                  # This file
├── QUICKSTART.md              # 5-minute clone → run → connect walkthrough
├── main.py                    # Local entry point (uv run python main.py)
├── pyproject.toml             # Package + dependencies (uv)
├── requirements.txt           # Mirror of runtime deps (for buildpack hosts)
├── Dockerfile                 # Container image (streamable-HTTP, port 8080)
├── manifest.yaml              # cloud.gov (Cloud Foundry) deploy
├── server.json                # MCP registry metadata
├── .env.example               # Copy to .env for local dev
├── src/
│   └── lrl_reservoirs/
│       ├── app.py             # FastMCP instance; picks stdio vs HTTP transport
│       ├── config.py          # Settings loaded from env / .env
│       ├── guide_curve.py     # CWMS seasonal guide-curve interpolation
│       ├── lakes.py           # Lake metadata table and LakeName enum
│       ├── lake_report.py     # Daily Lake Report HTML parser
│       ├── models.py          # Pydantic output models → MCP output schemas
│       ├── utils.py           # CWMS HTTP client, security helpers, pagination
│       ├── routes.py          # /health and /version endpoints
│       ├── tools/
│       │   ├── lake_conditions.py          # get_lake_conditions tool
│       │   ├── summarize_district_lakes.py # summarize_district_lakes tool (basin/status filters)
│       │   ├── list_lakes.py               # list_lakes tool (static lake directory)
│       │   ├── check_report.py             # check_against_daily_report tool
│       │   └── lake_trend.py               # get_lake_trend tool
│       ├── prompts/
│       │   └── briefing.py                 # district_briefing prompt
│       ├── resources/
│       │   └── lakes.py                    # lrl://lakes resource (lake directory)
│       └── data/
│           └── lrl_lakes.csv               # Lake metadata + CWMS timeseries IDs
├── tests/
│   ├── test_lake_conditions.py
│   ├── test_summarize_district_lakes.py
│   ├── test_models.py                    # every tool's output matches its model
│   ├── test_lake_trend.py
│   ├── test_briefing.py                  # district_briefing prompt + agent instructions
│   ├── test_check_report.py              # Report vs. live comparison, report client boundary
│   ├── test_percent_util_lrl_report.py   # Deterministic validation vs. saved CWMS responses
│   ├── test_fetch_lake_report.py         # HTML parser tests against saved report fixture
│   ├── test_http_security.py
│   ├── test_server.py
│   └── fixtures/
│       ├── lrl_oct8_2026/                # Saved CWMS responses for offline tests
│       └── lrl_lake_report_2026-10-08.txt  # Reference report (hand-transcribed)
├── eval/
│   ├── fetch_lake_report.py              # Download + parse the Daily Lake Report
│   ├── lrl_percent_util_validation.py    # Validate percent_util + guide curve vs. report
│   └── reports/                          # Dated report snapshots (CSV + raw HTML)
│       ├── lrl_lake_report_2026-10-08.csv  # Hand-transcribed from .txt fixture
│       └── lrl_lake_report_YYYY-MM-DD.csv  # Fetched by fetch_lake_report.py
└── deploy/
    ├── README.md
    ├── ibm/                   # watsonx Orchestrate kits
    └── databricks/            # Databricks Apps kit
```

---

## Setup

### Prerequisites

- [uv](https://docs.astral.sh/uv/) — `pip install uv` or `brew install uv`

No API keys required. The CWMS Data API is public.

### Install and run

```bash
cp .env.example .env
uv sync
uv run python main.py
```

The server starts in **stdio** mode — it speaks JSON-RPC over stdin/stdout, which
is how local clients (IBM Bob, Claude Desktop) launch it as a subprocess.

### Connect a client

**IBM Bob** — add the server to `.bob/mcp.json` in the repo root:

```json
{
  "mcpServers": {
    "lrl-reservoirs": {
      "command": "uv",
      "args": ["run", "lrl-reservoirs"],
      "cwd": "/absolute/path/to/this/repo"
    }
  }
}
```

**Claude Desktop** — add the same block to
`~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or
`%APPDATA%\Claude\claude_desktop_config.json` (Windows), then restart.

### Verify

```bash
uv sync --group dev
uv run pytest tests/ -v      # 85 tests
uv run ruff check .          # lint
uv run ruff format --check . # format check
```

---

## Data sources

All data comes from the public
[CWMS Data API](https://cwms-data.usace.army.mil/cwms-data/) operated by USACE.
The server requires `Accept: application/json;version=2` on every request (without
it the API returns `501 Not Implemented`).

### Elevation and guide curve

| Series / Level | Role |
|---|---|
| `<lake>.Elev.Inst.0.0.lrldlb-rev` | Current pool elevation; updates every 15–60 min |
| `<lake>.Elev.Inst.0.Bottom of Flood Control` | Seasonal guide curve elevation (interpolated) |

The guide curve is a CWMS _location level_ with seasonal anchor points. The server
interpolates it at the observation timestamp using the same linear-interpolation
algorithm as the CWMS engine.

### Storage and Percent Util

| Series / Level | Role |
|---|---|
| `<lake>.Stor.Inst.1Hour.0.lrldlb-comp` | Current storage (acre-ft), hourly |
| `<lake>.Stor.Inst.0.Bottom of Flood Control` | Storage at today's guide curve — seasonal level |
| `<lake>.Stor.Inst.0.Top of Flood` | Storage at flood pool — constant level |

**`lrldlb-comp` vs `lrldlb-rev`:** Both variants appear in the CWMS catalog.
The `-rev` (revised) series is present but returns no values through the public
API. The `-comp` (computed) series is the only variant with live data.

**Percent Util formula** (matches the USACE Daily Lake Report column exactly):

```
percent_util = (current_storage − storage_at_guide_curve)
             / (storage_at_flood_pool − storage_at_guide_curve) × 100
```

Negative values mean the pool is below the guide curve.
Both bounds are fetched live from CWMS storage location levels rather than
computed from elevation, because the elevation–storage relationship is non-linear.

**Report time zone:** The LRL Daily Lake Report is published at **06:00 US/Eastern
(EDT = UTC−4)**, equivalent to **10:00 UTC** in October. The validation fixtures
and eval script use 10:00 UTC as the reference time.

---

## Validation against the LRL Daily Lake Report

The USACE publishes the
[LRL Daily Lake Report](https://www.lrl-wc.usace.army.mil/reports/lkreport.html)
each morning at **06:00 US/Eastern** (10:00 UTC in October). Two scripts in
`eval/` let you download the report and compare it against live CWMS data:

| Script | Purpose |
|---|---|
| `eval/fetch_lake_report.py` | Download today's report → `eval/reports/lrl_lake_report_YYYY-MM-DD.csv` + raw HTML. Never overwrites an existing date's files. |
| `eval/lrl_percent_util_validation.py` | Read a dated CSV and validate **Percent Util** (±0.15 pp) and **guide curve elevation** (±0.10 ft) for all 17 lakes against live CWMS data. |

### Daily routine

Run these after 06:00 US/Eastern (the report publishes around that time):

```bash
uv run python eval/fetch_lake_report.py
uv run python eval/lrl_percent_util_validation.py --date $(date +%F)
```

`fetch_lake_report.py` is safe to re-run — it skips the download if today's
files already exist. Both commands exit `0` on success and `1` on any failure.

### The `eval/reports/` folder

Each downloaded report is saved as two immutable files:

```
eval/reports/
├── lrl_lake_report_2026-10-08.csv   # hand-transcribed from test fixture
├── lrl_lake_report_2026-10-09.csv   # fetched by fetch_lake_report.py
├── lrl_lake_report_2026-10-09.html  # raw HTML provenance copy
└── …
```

CSV columns: `lake, basin, winter_pool, summer_pool, flood_pool, todays_pool,
dev_from_pool, change_24hr, inflow_24hr, outflow, percent_util`

The guide curve reference used in the elevation check is derived as
`todays_pool − dev_from_pool` (the report rounds `dev_from_pool` to one decimal
place, which accounts for the ±0.10 ft tolerance).

### Validation results (2026-10-08)

Largest Percent Util deviation: **−0.08** (Barren and CarrCreek); 15 of 17 lakes
matched at the reported 2-decimal-place precision. All 17 guide curve elevations
matched within **0.10 ft**.

| Lake | Report % | Calc % | Δ |
|---|---:|---:|---:|
| CaesarCreek | 0.05 | 0.05 | 0.00 |
| WHHarsha | −0.05 | −0.05 | 0.00 |
| WestFork | 0.24 | 0.24 | 0.00 |
| CJBrown | 0.79 | 0.79 | 0.00 |
| Brookville | 0.48 | 0.48 | 0.00 |
| CaveRun | 2.62 | 2.62 | 0.00 |
| CarrCreek | 7.20 | 7.12 | −0.08 |
| Buckhorn | 0.94 | 0.94 | 0.00 |
| Taylorsville | −0.68 | −0.68 | 0.00 |
| Green | −0.85 | −0.85 | 0.00 |
| Nolin | 1.33 | 1.33 | 0.00 |
| Barren | 0.95 | 0.87 | −0.08 |
| Rough | −0.18 | −0.18 | 0.00 |
| CMHarden | −0.10 | −0.10 | 0.00 |
| CaglesMill | 0.18 | 0.18 | 0.00 |
| Monroe | −0.37 | −0.37 | 0.00 |
| Patoka | 21.77 | 21.77 | 0.00 |

### Deterministic offline test

`tests/test_percent_util_lrl_report.py` replays saved CWMS responses from
`tests/fixtures/lrl_oct8_2026/` and asserts all 17 lakes are within ±0.15 of
the report value. To refresh the fixture with live data:

```bash
uv run python eval/lrl_percent_util_validation.py --date 2026-10-08 --save-fixtures
```

---

## Limitations

- **Storage timeseries lag:** The `lrldlb-comp` series updates hourly. Sub-hourly
  elevation changes are not reflected in storage until the next hourly value.
- **No storage-to-elevation ratings:** CWMS does not expose elevation→storage
  rating tables for LRL reservoirs through the public API. Storage is read
  directly from the computed timeseries.
- **`percent_to_flood_pool` is elevation-based:** It is computed from the pool
  stage (elevation), not storage. Use `percent_util` when you need a value that
  matches the Daily Lake Report.
- **Guide curve `no_guide` fallback:** When the Bottom of Flood Control level
  fetch fails, the server falls back to a static pool classification
  (`pool_status` is prefixed `no_guide/`). `percent_util` is still computed from
  storage as long as the storage bounds are available.
- **Public data, no auth:** The CWMS Data API is unauthenticated. Data is
  subject to revision (the `-rev` series) but revised values are currently not
  served through the public endpoint.

---

## Security notes

- **Outbound requests are constrained.** All CWMS calls use a fixed base URL in
  operator-controlled code. Tool arguments cannot influence the destination host
  or scheme. Path segments are percent-encoded before use.
- **Redirects are disabled.** `httpx` is configured with
  `follow_redirects=False`. The CWMS API does not redirect; a redirect would
  indicate something unexpected.
- **Responses are size-limited.** Each response is capped at 1 MB to prevent
  memory exhaustion from unexpectedly large upstream payloads.
- **Errors are sanitised.** Upstream response bodies, full URLs, and stack
  traces are never forwarded to the caller. Error messages expose only the HTTP
  status code or a generic category.
- **DNS and IP validation** is not needed here because the destination is a
  fixed hardcoded constant, not a caller-supplied URL.

See [SECURITY.md](SECURITY.md) for the vulnerability disclosure policy.

---

## Deploying

Local development uses stdio. To deploy for an agent platform:

- **IBM watsonx Orchestrate** — [deploy/ibm/](deploy/ibm/) (three kits: local
  stdio toolkit, Code Engine build-from-Git, prebuilt image).
- **Databricks Apps** — [deploy/databricks/](deploy/databricks/).
- **cloud.gov** — `cf push` with the included `manifest.yaml`.

All kits run the same server code. `app.py` auto-selects HTTP when the platform
injects `DATABRICKS_APP_PORT` or `PORT`.

---

## License

[MIT](LICENSE). See [SECURITY.md](SECURITY.md) for the vulnerability disclosure
policy.
