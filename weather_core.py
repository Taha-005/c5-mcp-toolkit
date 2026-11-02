"""Core logic for the Weather Outing Planner (no MCP code in this file).

Why a separate module?
- The MCP tools in server.py stay thin wrappers (name, description, typed args).
- The Orchestration tool reuses these functions directly instead of
  duplicating the Data/Action logic.
- Unit tests can exercise the logic without starting an MCP server.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal

import httpx
from pydantic import BaseModel, Field

log = logging.getLogger("weather_planner")

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
MAX_FORECAST_DAYS = 14
HTTP_TIMEOUT_S = 10.0
RAIN_LIMIT_PCT = 60  # days with a higher rain probability are unsuitable for outings

Activity = Literal["hiking", "beach", "picnic", "sightseeing"]
IDEAL_HIGH_C: dict[str, int] = {"hiking": 18, "beach": 29, "picnic": 23, "sightseeing": 21}


# --------------------------------------------------------------------------- #
# Errors: one class per predictable failure, each with a stable error code.
# --------------------------------------------------------------------------- #
class WeatherPlannerError(Exception):
    code = "PLANNER_ERROR"


class InvalidInputError(WeatherPlannerError):
    code = "INVALID_INPUT"


class CityNotFoundError(WeatherPlannerError):
    code = "CITY_NOT_FOUND"


class WeatherServiceError(WeatherPlannerError):
    code = "SERVICE_UNAVAILABLE"


class NoSuitableDayError(WeatherPlannerError):
    code = "NO_SUITABLE_DAY"


class StorageError(WeatherPlannerError):
    code = "STORAGE_ERROR"


# --------------------------------------------------------------------------- #
# Typed outputs
# --------------------------------------------------------------------------- #
class DailyForecast(BaseModel):
    date: str = Field(description="ISO date, YYYY-MM-DD (city's local time zone)")
    condition: str = Field(description="Human-readable weather condition")
    temp_max_c: float
    temp_min_c: float
    precipitation_probability_pct: int
    wind_speed_max_kmh: float


class Forecast(BaseModel):
    city: str
    country: str
    latitude: float
    longitude: float
    timezone: str
    days: list[DailyForecast]


class SavedPlan(BaseModel):
    id: int
    city: str
    date: str
    activity: str
    note: str
    saved_at: str
    already_existed: bool = Field(
        default=False,
        description="True if an identical plan was already saved (no duplicate written)",
    )


class OutingPlan(BaseModel):
    chosen_day: DailyForecast
    reason: str
    days_considered: int
    saved_plan: SavedPlan


# WMO weather interpretation codes used by Open-Meteo (abridged).
WEATHER_CODES = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Rime fog", 51: "Light drizzle", 53: "Drizzle", 55: "Dense drizzle",
    61: "Light rain", 63: "Rain", 65: "Heavy rain", 71: "Light snow", 73: "Snow",
    75: "Heavy snow", 80: "Rain showers", 81: "Heavy rain showers", 82: "Violent rain showers",
    95: "Thunderstorm", 96: "Thunderstorm with hail", 99: "Severe thunderstorm with hail",
}


# --------------------------------------------------------------------------- #
# HTTP helper: converts every network problem into one clear error type.
# --------------------------------------------------------------------------- #
async def _get_json(url: str, params: dict) -> dict:
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_S) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            return resp.json()
    except httpx.TimeoutException as e:
        raise WeatherServiceError(
            f"Open-Meteo did not respond within {HTTP_TIMEOUT_S:.0f} s. Try again shortly."
        ) from e
    except httpx.HTTPStatusError as e:
        raise WeatherServiceError(f"Open-Meteo returned HTTP {e.response.status_code}.") from e
    except httpx.HTTPError as e:
        raise WeatherServiceError(
            f"Could not reach Open-Meteo ({type(e).__name__}). Check the internet connection."
        ) from e


# --------------------------------------------------------------------------- #
# DATA logic: read-only
# --------------------------------------------------------------------------- #
async def get_forecast(city: str, days: int = 7) -> Forecast:
    """Return a daily forecast for `city` covering `days` days (1-14). Read-only."""
    city = (city or "").strip()
    if not city:
        raise InvalidInputError("City must be a non-empty name, e.g. 'Beirut'.")
    if not isinstance(days, int) or not 1 <= days <= MAX_FORECAST_DAYS:
        raise InvalidInputError(f"days must be an integer from 1 to {MAX_FORECAST_DAYS}; got {days!r}.")

    geo = await _get_json(GEOCODE_URL, {"name": city, "count": 1, "language": "en", "format": "json"})
    results = geo.get("results") or []
    if not results:
        raise CityNotFoundError(
            f"No location matches '{city}'. Check the spelling or try a nearby larger city."
        )
    place = results[0]

    data = await _get_json(
        FORECAST_URL,
        {
            "latitude": place["latitude"],
            "longitude": place["longitude"],
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,"
                     "precipitation_probability_max,wind_speed_10m_max",
            "timezone": "auto",
            "forecast_days": days,
        },
    )
    d = data["daily"]
    forecast_days = [
        DailyForecast(
            date=d["time"][i],
            condition=WEATHER_CODES.get(d["weather_code"][i], f"Code {d['weather_code'][i]}"),
            temp_max_c=d["temperature_2m_max"][i],
            temp_min_c=d["temperature_2m_min"][i],
            precipitation_probability_pct=int(d["precipitation_probability_max"][i] or 0),
            wind_speed_max_kmh=d["wind_speed_10m_max"][i],
        )
        for i in range(len(d["time"]))
    ]
    return Forecast(
        city=place["name"],
        country=place.get("country", ""),
        latitude=place["latitude"],
        longitude=place["longitude"],
        timezone=data.get("timezone", "auto"),
        days=forecast_days,
    )


# --------------------------------------------------------------------------- #
# ACTION logic: writes to a local JSON file
# --------------------------------------------------------------------------- #
def plans_file() -> Path:
    default = Path(__file__).parent / "data" / "plans.json"
    return Path(os.environ.get("PLANS_FILE", default))


def list_plans() -> list[SavedPlan]:
    path = plans_file()
    if not path.exists():
        return []
    try:
        return [SavedPlan(**p) for p in json.loads(path.read_text(encoding="utf-8"))]
    except (json.JSONDecodeError, TypeError, ValueError) as e:
        raise StorageError(f"Plans file {path} is corrupted; fix or delete it.") from e


def save_plan(city: str, date_str: str, activity: str, note: str = "") -> SavedPlan:
    """Persist an outing plan. Idempotent: an identical plan is not duplicated."""
    city = (city or "").strip()
    if not city:
        raise InvalidInputError("City must be a non-empty name.")
    if activity not in IDEAL_HIGH_C:
        raise InvalidInputError(f"activity must be one of {sorted(IDEAL_HIGH_C)}; got {activity!r}.")
    try:
        plan_date = date.fromisoformat(date_str)
    except (TypeError, ValueError) as e:
        raise InvalidInputError(f"date must be in YYYY-MM-DD format; got {date_str!r}.") from e
    # Allow "yesterday" to tolerate time-zone differences between the user and the city.
    if plan_date < date.today() - timedelta(days=1):
        raise InvalidInputError(f"date {date_str} is in the past; plans must be for today or later.")
    if len(note) > 300:
        raise InvalidInputError("note must be at most 300 characters.")

    plans = list_plans()
    for p in plans:
        if (p.city.lower(), p.date, p.activity) == (city.lower(), date_str, activity):
            return p.model_copy(update={"already_existed": True})

    plan = SavedPlan(
        id=max((p.id for p in plans), default=0) + 1,
        city=city,
        date=date_str,
        activity=activity,
        note=note,
        saved_at=datetime.now().isoformat(timespec="seconds"),
    )
    path = plans_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps([p.model_dump(exclude={"already_existed"}) for p in plans + [plan]], indent=2),
            encoding="utf-8",
        )
    except OSError as e:
        raise StorageError(f"Could not write plans file {path}: {e}") from e
    log.info("Saved plan #%d: %s on %s (%s)", plan.id, city, date_str, activity)
    return plan


# --------------------------------------------------------------------------- #
# ORCHESTRATION logic: Data -> decide -> Action
# --------------------------------------------------------------------------- #
def score_day(day: DailyForecast, activity: str) -> float | None:
    """Lower is better; None means the day is unsuitable (too rainy)."""
    if day.precipitation_probability_pct > RAIN_LIMIT_PCT:
        return None
    return (
        day.precipitation_probability_pct
        + 3 * abs(day.temp_max_c - IDEAL_HIGH_C[activity])
        + max(0.0, day.wind_speed_max_kmh - 30)
    )


async def plan_outing(city: str, activity: str, days: int = 7, note: str = "") -> OutingPlan:
    """Find the best day for `activity` in `city` within `days` days, then save it."""
    if activity not in IDEAL_HIGH_C:
        raise InvalidInputError(f"activity must be one of {sorted(IDEAL_HIGH_C)}; got {activity!r}.")

    forecast = await get_forecast(city, days)  # step 1: Data
    scored = [(s, d) for d in forecast.days if (s := score_day(d, activity)) is not None]
    if not scored:
        raise NoSuitableDayError(
            f"All {len(forecast.days)} days in {forecast.city} have more than {RAIN_LIMIT_PCT}% "
            "chance of rain. Nothing was saved; try a longer window or another city."
        )
    _, best = min(scored, key=lambda pair: pair[0])  # step 2: decide
    reason = (
        f"{best.date}: {best.condition.lower()}, high {best.temp_max_c:.0f}°C "
        f"(ideal for {activity} is about {IDEAL_HIGH_C[activity]}°C), "
        f"{best.precipitation_probability_pct}% rain chance, wind {best.wind_speed_max_kmh:.0f} km/h. "
        f"Best of {len(forecast.days)} days checked."
    )
    saved = save_plan(forecast.city, best.date, activity, note or f"Auto-planned. {reason}")  # step 3: Action
    return OutingPlan(chosen_day=best, reason=reason, days_considered=len(forecast.days), saved_plan=saved)
