"""MCP-level tests: call the tools through a real MCP client.

1. In-memory FastMCP client: checks tool discovery, schemas, structured output, error codes, resource.
2. STDIO round trip with the OpenAI Agents SDK's MCPServerStdio: the same client class agent.py uses
   (no LLM and no network needed).
"""

import json
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from server import mcp

TOMORROW = (date.today() + timedelta(days=1)).isoformat()


async def test_exactly_three_tools_are_discovered():
    async with Client(mcp) as client:
        tools = {t.name: t for t in await client.list_tools()}
    assert set(tools) == {"get_weather_forecast", "save_plan", "plan_outing"}
    assert tools["get_weather_forecast"].annotations.read_only_hint is True
    assert tools["save_plan"].annotations.read_only_hint is False
    for t in tools.values():
        assert t.description and t.output_schema  # documented, typed output


async def test_forecast_tool_through_mcp(fake_api):
    async with Client(mcp) as client:
        result = await client.call_tool("get_weather_forecast", {"city": "Beirut", "days": 2})
    assert result.structured_content["city"] == "Beirut"
    assert len(result.structured_content["days"]) == 2


async def test_schema_rejects_days_out_of_range():
    async with Client(mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool("get_weather_forecast", {"city": "Beirut", "days": 99})


async def test_city_not_found_returns_coded_error(fake_api):
    fake_api.city_found = False
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match=r"\[CITY_NOT_FOUND\]"):
            await client.call_tool("get_weather_forecast", {"city": "Atlantisville"})


async def test_save_plan_bad_date_returns_coded_error():
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match=r"\[INVALID_INPUT\]"):
            await client.call_tool("save_plan", {"city": "Beirut", "date": "next friday", "activity": "picnic"})


async def test_orchestration_then_resource_shows_saved_plan(fake_api):
    async with Client(mcp) as client:
        result = await client.call_tool("plan_outing", {"city": "Beirut", "activity": "picnic", "days": 3})
        assert result.structured_content["saved_plan"]["id"] == 1
        contents = await client.read_resource("plans://saved")
    plans = json.loads(contents[0].text)
    assert len(plans) == 1 and plans[0]["activity"] == "picnic"


async def test_stdio_round_trip_with_agents_sdk_client(temp_plans_file):
    """Spawns server.py as a subprocess exactly like agent.py does."""
    from agents.mcp import MCPServerStdio

    server_path = Path(__file__).parent.parent / "server.py"
    async with MCPServerStdio(
        params={"command": sys.executable, "args": [str(server_path)],
                "env": {"PLANS_FILE": str(temp_plans_file)}},
        client_session_timeout_seconds=30,
    ) as server:
        names = {t.name for t in await server.list_tools()}
        assert names == {"get_weather_forecast", "save_plan", "plan_outing"}

        ok = await server.call_tool("save_plan", {"city": "Beirut", "date": TOMORROW, "activity": "hiking"})
        assert not ok.is_error and json.loads(ok.content[0].text)["id"] == 1

        bad = await server.call_tool("save_plan", {"city": "Beirut", "date": "2020-01-01", "activity": "hiking"})
        assert bad.is_error and "[INVALID_INPUT]" in bad.content[0].text
    assert temp_plans_file.exists()
