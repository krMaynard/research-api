# MCP server (`mcp_server.py`)

A native [Model Context Protocol](https://modelcontextprotocol.io) **stdio**
server that exposes this API's no-SQL query interface to MCP clients (Claude
Desktop, Claude Code, any MCP host), so an agent can explore the EU DSA VLOP
transparency dataset directly.

It is a **thin MCP front end over the running HTTP API** — every tool maps to a
real endpoint, so all queries go through the same `compile_query` trust boundary
as the web service: no SQL is ever accepted, every field/operation is validated
against the table registry, and all values are bound as parameters.

The server does **not** import the FastAPI app. It talks to the API over HTTP,
so it has a tiny dependency footprint (`mcp` + `httpx`), needs no database or
dataset, and stays clear of the app's `fastapi`/`starlette` version pins. (This
is the Python sibling of the generated Go MCP server in
[`clients/cli/`](../clients/cli/).)

## Tools

| Tool | Endpoint | Auth | What it does |
|------|----------|------|--------------|
| `list_tables` | `GET /api/explore/options` | — | Discover the DSA report tables + each one's dimensions, measures, and the available aggregate functions / composite options. **Start here.** |
| `describe_table` | `GET /api/schema/{table}` (keyed) or discovery (public) | — | Dimensions, measures, valid operations, and a runnable example for one table. Returns the full field registry when an API key is configured. |
| `dataset_overview` | `GET /api/overview` | — | Headline aggregates: period, service/platform counts, total notices, top platforms and categories. |
| `run_query` | `POST /api/explore` | — | Run a structured (no-SQL) query synchronously and return `{columns, rows, row_count, truncated}`. Supports single-table and composite (cross-table) shapes; row-capped. |
| `ask` | `POST /api/ask` | key | Natural-language question → LLM-generated *structured* query → results. Needs `TRANSPARENCY_API_KEY` and a server with `ANTHROPIC_API_KEY` set. |

`run_query` takes the same structured query object as `POST /api/query` /
`POST /api/explore`. Example single-table query:

```json
{
  "table": "t4_notices",
  "query": {"and": [{"operation": "EQ", "field_name": "platform", "field_values": ["Meta"]}]},
  "group_by": ["service_name"],
  "aggregates": [{"function": "SUM", "field_name": "notices", "alias": "total_notices"}],
  "sort": [{"field_name": "total_notices", "order": "desc"}],
  "max_count": 20
}
```

See the [main README](../README.md) → "No SQL — structured query parameters" for
the full query language (filters, aggregates, composite queries).

## Configuration

The server is configured entirely via environment variables:

| Variable | Default | Purpose |
|----------|---------|---------|
| `TRANSPARENCY_API_URL` | `http://127.0.0.1:8000` | Base URL of a running API. |
| `TRANSPARENCY_API_KEY` | _(unset)_ | Optional `X-API-Key`. When set, keyed tools (`describe_table` full registry, `ask`) use the authenticated endpoints. |
| `TRANSPARENCY_API_TIMEOUT` | `30` | Per-request timeout in seconds. |

## Run it

The MCP SDK pulls a newer `starlette` than the API pins, so install it into its
own virtualenv (this is also what `make mcp` does):

```bash
python -m venv .venv-mcp && . .venv-mcp/bin/activate
pip install -r requirements-mcp.txt

# Point it at a running server (start one with `make serve` in another terminal).
export TRANSPARENCY_API_URL=http://127.0.0.1:8000
# Optional — enables `ask` and the full describe_table registry:
export TRANSPARENCY_API_KEY=momo

python mcp_server.py        # speaks MCP over stdio
```

`python mcp_server.py` communicates over stdio (stdin/stdout); logs go to
stderr. It is meant to be launched by an MCP host, not used interactively.

## Register with an MCP host

### Claude Desktop

Add an entry to `claude_desktop_config.json` (see the example in
[`mcp-config.example.json`](../mcp-config.example.json)). Point `command` at the
`python` inside `.venv-mcp` and `args` at `mcp_server.py`:

```json
{
  "mcpServers": {
    "transparency-report-api": {
      "command": "/absolute/path/to/transparency-report-api/.venv-mcp/bin/python",
      "args": ["/absolute/path/to/transparency-report-api/mcp_server.py"],
      "env": {
        "TRANSPARENCY_API_URL": "http://127.0.0.1:8000",
        "TRANSPARENCY_API_KEY": "momo"
      }
    }
  }
}
```

### Claude Code

```bash
claude mcp add transparency-report-api \
  -e TRANSPARENCY_API_URL=http://127.0.0.1:8000 \
  -e TRANSPARENCY_API_KEY=momo \
  -- /absolute/path/to/.venv-mcp/bin/python /absolute/path/to/mcp_server.py
```

The API must be reachable at `TRANSPARENCY_API_URL` while the host is using the
tools.

## Tests

`test_mcp_server.py` exercises the tool functions end-to-end against the real
FastAPI app via an in-process `TestClient` — no network, no running server, and
no `mcp` SDK required (only `build_server()` imports the SDK, and that test
self-skips when it's absent). It runs in the normal CI suite.
