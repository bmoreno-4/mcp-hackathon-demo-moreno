# Quickstart — LRL Reservoir Conditions MCP Server

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

No API keys needed — the CWMS Data API is public.

## 3. Run the server

```bash
uv run python main.py
```

The server starts in **stdio** mode and waits for a client to connect over
stdin/stdout. That's correct — it doesn't print a URL, because a local MCP
server is launched *by* the client as a subprocess. Press `Ctrl-C` to stop it.

## 4. Connect a client

### Claude Code / IBM Bob

Create or edit `.mcp.json` in the repo root (or your user MCP settings) with
the **absolute path** to this repo:

```json
{
  "mcpServers": {
    "lrl-reservoirs": {
      "command": "uv",
      "args": ["run", "lrl-reservoirs"],
      "cwd": "/absolute/path/to/lrl-reservoirs"
    }
  }
}
```

### Claude Desktop

Add the same block to
`~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or
`%APPDATA%\Claude\claude_desktop_config.json` (Windows), then restart Claude
Desktop.

Once connected, try asking:

> "How is Barren River Lake doing compared to its guide curve?"

> "Which LRL lakes are currently below their guide curve?"

> "Give me a district summary — how many lakes are above guide?"

## 5. Verify everything

```bash
uv sync --group dev
uv run pytest tests/ -v      # 85 tests pass
uv run ruff check .          # lint clean
uv run ruff format --check . # format clean
```

## 6. Test HTTP mode (optional)

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
- Pool status (`below_guide`, `at_guide`, `above_guide`, `at_or_above_flood`)
- Current storage (acre-ft) and storage-based Percent Util
- Static reference levels (winter/summer/flood pool)

### `summarize_district_lakes`

No parameters. Queries all 17 lakes in parallel and returns:

- Per-lake conditions (same shape as `get_lake_conditions`)
- Aggregate counts: how many lakes are above/at/below guide or at flood stage
- Sorted by basin then lake name for easy scanning

---

## Next steps

- **Deploy it** — pick a kit in [deploy/README.md](deploy/README.md)
  (IBM watsonx Orchestrate or Databricks).
- **Read the full docs** — [README.md](README.md) has data source details,
  validation results, and security notes.
