# Quickstart: LRL Reservoir Conditions MCP Server

Get from clone to a running server connected to a client in about 5 minutes.

## 1. Install prerequisites

You need [uv](https://docs.astral.sh/uv/) (a fast Python package manager):

```bash
pip install uv     # or: brew install uv
```

## 2. Set up the project

```bash
cp .env.example .env
uv sync
```

No API keys are needed. The CWMS Data API is public.

## 3. Run the server

```bash
uv run python main.py
```

The server starts in **stdio** mode and waits for a client to connect over
stdin/stdout. It does not print a URL, because a local MCP
server is launched *by* the client as a subprocess. Press `Ctrl-C` to stop it.

## 4. Connect a client

### IBM Bob

Create or edit `.bob/mcp.json` in the repo root with the absolute path to
this repo:

```json
{
  "mcpServers": {
    "lrl-reservoirs": {
      "command": "uv",
      "args": ["run", "lrl-reservoirs"],
      "cwd": "/absolute/path/to/mcp-hackathon-demo-moreno"
    }
  }
}
```

### Claude Desktop

Add the same block to
`~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or
`%APPDATA%\Claude\claude_desktop_config.json` (Windows), then restart Claude
Desktop.

On macOS, if the repo is in `~/Documents` (or another iCloud-synced folder),
the client may report "Server disconnected" because macOS hides the file that
tells Python where the package is. Add the source folder to the server's
environment:

```json
"env": { "PYTHONPATH": "/absolute/path/to/mcp-hackathon-demo-moreno/src" }
```

Once connected, try asking:

> "How is Barren River Lake doing compared to its guide curve?"

> "Which LRL lakes are currently below their guide curve?"

> "Give me a district summary: how many lakes are above guide?"

> "Give me today's lake briefing."

## 5. Validate against the Daily Lake Report

The USACE publishes the
[LRL Daily Lake Report](https://www.lrl-wc.usace.army.mil/reports/lkreport.html)
each morning at **06:00 US/Eastern**. After it appears, download it and
compare it against live CWMS data:

```bash
uv run python eval/fetch_lake_report.py
uv run python eval/lrl_percent_util_validation.py --date $(TZ=America/New_York date +%F)
```

The first command saves `eval/reports/lrl_lake_report_YYYY-MM-DD.csv` plus the
raw HTML. It is safe to re-run: it never overwrites a saved report, and if
USACE has revised the report since, it saves the new version as
`lrl_lake_report_YYYY-MM-DD_rev-HHMM`. The second command checks Percent Util
and the guide curve for all 17 lakes against live CWMS data and exits `0` if
all pass. `TZ=America/New_York` keeps the date right on machines set to UTC.

## 6. Verify everything

```bash
uv sync --group dev
uv run pytest tests/ -q      # full suite (~185 tests)
uv run ruff check .          # lint clean
uv run ruff format --check . # format clean
```

## 7. Test HTTP mode (optional)

Cloud hosts run the server over HTTP. Try it locally:

```bash
MCP_TRANSPORT=streamable-http uv run python main.py
# in another terminal:
curl http://localhost:8000/health      # {"status":"healthy",...}
```

---

## What the tools return

### `get_lake_conditions`

Pass a CWMS location ID (e.g. `Barren`, `Taylorsville`, `Patoka`). Returns:

- Pool elevation, vertical datum, and observation timestamp
- Today's seasonal guide curve elevation and deviation from it
- Pool status (`below_guide`, `at_guide`, `above_guide`, `at_or_above_flood`,
  `no_data`, or `no_guide/...` if the guide curve could not be read)
- Reading time (UTC and as an Eastern label such as "Wednesday, October 21,
  2026, 6:00 AM EDT"), its age, and a `stale` flag after 30 hours
- Current storage (acre-ft) and storage-based Percent Util
- Static reference levels (winter/summer/flood pool)

### `summarize_district_lakes`

Optional `basin` and `status` filters. Queries all 17 lakes in parallel and
returns:

- Per-lake conditions (same shape as `get_lake_conditions`), sorted by basin
  then lake name
- Counts for the whole district: above, at or below guide, at or above flood
  pool, no data
- `lakes_by_status`: the lake names for each status, each lake listed once
- `flood_storage_ranking`: lakes by Percent Util, highest first
- `unevaluated_lakes`: lakes with no data, so they are never reported as "none"

### `list_lakes`

Optional `basin`. Static directory of the 17 lakes: official name, basin,
winter, summer and flood pool. No network call.

### `get_lake_trend`

Pass a lake and optionally `days` (1-14, default 3). Returns:

- Start and latest elevation, guide curve deviation and Percent Util, and the change
- 24-hour change and `rising` / `falling` / `steady` (within 0.1 ft)
- Whether the pool moved toward or away from its guide curve
- Peak, low and every elevation reading in the window

### `district_briefing` (prompt)

Optional `basin`. A ready-made daily briefing that calls
`check_against_daily_report`, `summarize_district_lakes` and `get_lake_trend`
and writes the result in fixed sections. watsonx Orchestrate does not show MCP
prompts, so the same steps are in the agent instructions.

### `check_against_daily_report`

Optional `lake`. Fetches today's LRL Daily Lake Report (published for 06:00
Eastern) and compares each lake's pool, guide curve and Percent Util with live
CWMS values:

- Status per lake: `matches`, `changed_since_report` (live data is newer and
  has moved, for example after rain), `differs`, `no_live_data`,
  `not_in_report`
- The report's 24-hour precipitation, 24-hour change, inflow and 6 AM outflow
- `rain_last_24hr`: every lake with rain, wettest first
- `changes_since_report`: the lakes that moved since 6 AM and which values moved

---

## Next steps

- To deploy, pick a kit in [deploy/README.md](deploy/README.md). This project
  uses the IBM watsonx Orchestrate local toolkit, with agent instructions in
  `deploy/ibm/local-mcp-toolkit/agent_instructions.md`.
- [README.md](README.md) has the data sources, validation results, agent
  evaluation and security notes.
