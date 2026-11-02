"""Weather Outing Planner: FastMCP server exposing exactly three tools + one resource.

    DATA tool           get_weather_forecast   read-only lookup (Open-Meteo)
    ACTION tool         save_plan              writes a plan to data/plans.json
    ORCHESTRATION tool  plan_outing            forecast -> pick best day -> save
    RESOURCE            plans://saved          pure data view of saved plans

Run standalone:  uv run server.py   (STDIO transport; normally launched by agent.py)
"""

import json
import logging
import sys
from typing import Annotated

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import Field

import weather_core as core

# With the STDIO transport, stdout carries the MCP protocol messages, so all
# logs must go to stderr or they would corrupt the protocol stream.
logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(asctime)s [server] %(message)s")
log = logging.getLogger("weather_planner")

mcp = FastMCP("weather-outing-planner")


def _fail(tool: str, e: core.WeatherPlannerError) -> ToolError:
    """Log the failure and convert it to an MCP tool error with a stable error code."""
    log.warning("%s FAILED [%s] %s", tool, e.code, e)
    return ToolError(f"[{e.code}] {e}")


@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
async def get_weather_forecast(
    city: Annotated[str, Field(description="City name, e.g. 'Beirut' or 'Paris'.")],
    days: Annotated[int, Field(description="Number of forecast days, 1 to 14.", ge=1, le=14)] = 7,
) -> core.Forecast:
    """[DATA tool] Look up the daily weather forecast for a city. Read-only: changes nothing.

    Returns the resolved city, country, coordinates, and for each day: date,
    condition, max/min temperature (°C), max precipitation probability (%),
    and max wind speed (km/h).
    Errors: INVALID_INPUT, CITY_NOT_FOUND, SERVICE_UNAVAILABLE.
    """
    log.info("get_weather_forecast(city=%r, days=%r)", city, days)
    try:
        return await core.get_forecast(city, days)
    except core.WeatherPlannerError as e:
        raise _fail("get_weather_forecast", e) from e


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True})
def save_plan(
    city: Annotated[str, Field(description="City the outing takes place in.")],
    date: Annotated[str, Field(description="Outing date in YYYY-MM-DD format (today or later).")],
    activity: Annotated[core.Activity, Field(description="Type of outing.")],
    note: Annotated[str, Field(description="Optional short note (max 300 characters).")] = "",
) -> core.SavedPlan:
    """[ACTION tool] Save an outing plan to the local plans file. Side effect: writes data/plans.json.

    Idempotent: saving an identical plan (same city, date, activity) returns the
    existing plan with already_existed=true instead of creating a duplicate.
    Does NOT check the weather; use plan_outing for weather-based planning.
    Errors: INVALID_INPUT (bad or past date, unknown activity), STORAGE_ERROR.
    """
    log.info("save_plan(city=%r, date=%r, activity=%r)", city, date, activity)
    try:
        return core.save_plan(city, date, activity, note)
    except core.WeatherPlannerError as e:
        raise _fail("save_plan", e) from e


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "openWorldHint": True})
async def plan_outing(
    city: Annotated[str, Field(description="City to plan the outing in.")],
    activity: Annotated[core.Activity, Field(description="Type of outing.")],
    days: Annotated[int, Field(description="How many upcoming days to consider, 1 to 14.", ge=1, le=14)] = 7,
) -> core.OutingPlan:
    """[ORCHESTRATION tool] Pick the best upcoming day for an activity, then save it as a plan.

    Coordinates the other two tools' logic: (1) fetches the forecast (Data),
    (2) scores each day for the activity (rain chance, closeness to the
    activity's ideal temperature, wind), (3) saves the best day (Action).
    Use when the user wants the best day chosen for them. Side effect: saves one plan.
    Errors: INVALID_INPUT, CITY_NOT_FOUND, SERVICE_UNAVAILABLE,
    NO_SUITABLE_DAY (every day too rainy; nothing is saved), STORAGE_ERROR.
    """
    log.info("plan_outing(city=%r, activity=%r, days=%r)", city, activity, days)
    try:
        return await core.plan_outing(city, activity, days)
    except core.WeatherPlannerError as e:
        raise _fail("plan_outing", e) from e


@mcp.resource("plans://saved", mime_type="application/json", description="All saved outing plans.")
def saved_plans() -> str:
    """Pure data lookup: no computation, no side effects."""
    return json.dumps([p.model_dump(exclude={"already_existed"}) for p in core.list_plans()], indent=2)


if __name__ == "__main__":
    mcp.run(show_banner=False)  # STDIO transport by default
