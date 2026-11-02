
# C5: Weather Outing Planner (MCP Toolkit)

EECE 503P/798S Agentic Systems, Assignment C5. A FastMCP server with exactly three tools, connected to an OpenAI Agents SDK agent (running on Groq) that acts as the MCP Host/Client.

- **Part 1 summary:** [PART1_SUMMARY.md](PART1_SUMMARY.md)
- **Reflection:** [REFLECTION.md](REFLECTION.md)

## Architecture

```
User ─▶ agent.py (Host: Agents SDK agent on Groq)
           └─ MCPServerStdio (MCP Client) ──STDIO──▶ server.py (FastMCP Server)
                                                        └─ weather_core.py ─▶ Open-Meteo API / data/plans.json
```

## The three tools

| Category      | Tool                                         | What it does                                                               | Side effects               | Failure cases (error codes)                                               |
| ------------- | -------------------------------------------- | -------------------------------------------------------------------------- | -------------------------- | ------------------------------------------------------------------------- |
| Data          | `get_weather_forecast(city, days=7)`       | Daily forecast (condition, temps, rain %, wind) for 1–14 days             | None (read-only)           | `INVALID_INPUT`, `CITY_NOT_FOUND`, `SERVICE_UNAVAILABLE`            |
| Action        | `save_plan(city, date, activity, note="")` | Saves an outing plan; idempotent (no duplicates)                           | Writes `data/plans.json` | `INVALID_INPUT` (bad or past date, unknown activity), `STORAGE_ERROR` |
| Orchestration | `plan_outing(city, activity, days=7)`      | Fetches the forecast, scores each day for the activity, saves the best day | Saves one plan             | All of the above + `NO_SUITABLE_DAY` (nothing saved)                    |

Activities: `hiking`, `beach`, `picnic`, `sightseeing`. Saved plans are also exposed as the read-only resource `plans://saved`.

Every tool has a description, typed arguments (with ranges), a typed Pydantic output, and MCP annotations (`readOnlyHint`, `idempotentHint`) documenting side effects. Failures are raised as MCP tool errors with a stable code, e.g. `[CITY_NOT_FOUND] No location matches 'Atlantisville'...`.

## Setup

Requirements: [uv](https://docs.astral.sh/uv/), a free [Groq API key](https://console.groq.com/keys), and internet access (Open-Meteo needs no key).

```bash
git clone https://github.com/YOUR_USERNAME/c5-mcp-toolkit.git
cd c5-mcp-toolkit
uv sync
```

Create a `.env` file in the project root:

```
GROQ_API_KEY=gsk_your_key_here
GROQ_MODEL=openai/gpt-oss-120b
```

`GROQ_MODEL` must be a Groq model that supports tool calling. Groq retires models over time; to list the ones available to your key:

```bash
uv run python -c "from dotenv import load_dotenv; load_dotenv(); import os; from openai import OpenAI; c=OpenAI(base_url='https://api.groq.com/openai/v1', api_key=os.environ['GROQ_API_KEY']); print('\n'.join(sorted(m.id for m in c.models.list())))"
```

## Run

```bash
uv run agent.py        # interactive agent; launches server.py automatically over STDIO
uv run pytest -v       # 27 tests (no network or API key needed)
```

Example queries (one per tool, plus a failure case):

1. `What's the weather in Beirut for the next 3 days?` → Data
2. `Save a picnic in Jounieh on 2026-11-07` → Action
3. `Find me the best day for hiking in Ehmej this week and save it` → Orchestration
4. `What's the weather in Atlantisville?` → `CITY_NOT_FOUND`

The terminal prints each tool call and result, showing the full round trip: agent → MCP client → MCP server → back.

## Tests

| File                     | What it covers                                                                                                                                                                                                                                                      |
| ------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `tests/test_core.py`   | Logic of each tool: success cases; every failure case (unknown city, days out of range, empty city, API unreachable, bad/past date, unknown activity, corrupted plans file, no suitable day); idempotency; best-day selection                                       |
| `tests/test_server.py` | Through real MCP clients: exactly three tools discovered with descriptions and output schemas, structured results, coded errors, the `plans://saved` resource, and a STDIO round trip using the Agents SDK's `MCPServerStdio` (the same client as `agent.py`) |
| `tests/conftest.py`    | Isolates each test with a temporary plans file and replaces the network with canned Open-Meteo data                                                                                                                                                                 |

## Project structure

```
weather_core.py   core logic, typed models, error classes (no MCP code)
server.py         FastMCP server: 3 @mcp.tool wrappers + 1 @mcp.resource
agent.py          Agents SDK host/client on Groq, interactive loop with round-trip logging
tests/            pytest suite
```

## Notes

- Uses the standalone `fastmcp` package: in `mcp` v2 the built-in `mcp.server.fastmcp` module was renamed.
- Server logs go to stderr because, with the STDIO transport, stdout carries the MCP protocol messages.
- Agents SDK tracing is disabled because it uploads to OpenAI; the round trip is printed locally instead.
