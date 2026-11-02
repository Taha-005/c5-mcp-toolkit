"""Unit tests for the core logic of each tool, including the predictable failure cases."""

from datetime import date, timedelta

import httpx
import pytest

import weather_core as core
from tests.conftest import make_daily

TOMORROW = (date.today() + timedelta(days=1)).isoformat()


# ----------------------------- DATA: get_forecast ----------------------------- #
async def test_forecast_success(fake_api):
    fc = await core.get_forecast("Beirut", 3)
    assert fc.city == "Beirut" and fc.country == "Lebanon"
    assert len(fc.days) == 3
    assert fc.days[0].condition == "Light rain" and fc.days[1].condition == "Mainly clear"


async def test_forecast_city_not_found(fake_api):
    fake_api.city_found = False
    with pytest.raises(core.CityNotFoundError, match="No location matches"):
        await core.get_forecast("Atlantisville", 3)


@pytest.mark.parametrize("days", [0, 15, -2])
async def test_forecast_days_out_of_range(fake_api, days):
    with pytest.raises(core.InvalidInputError):
        await core.get_forecast("Beirut", days)
    assert fake_api.calls == []  # input is validated before any network call


async def test_forecast_empty_city(fake_api):
    with pytest.raises(core.InvalidInputError):
        await core.get_forecast("   ", 3)


async def test_forecast_service_unavailable(monkeypatch):
    """A network failure becomes a clear SERVICE_UNAVAILABLE error, not a crash or silence."""

    class DownClient:
        def __init__(self, *a, **k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, *a, **k): raise httpx.ConnectError("no route to host")

    monkeypatch.setattr(core.httpx, "AsyncClient", DownClient)
    with pytest.raises(core.WeatherServiceError, match="Could not reach Open-Meteo"):
        await core.get_forecast("Beirut", 3)


# ------------------------------ ACTION: save_plan ------------------------------ #
def test_save_plan_writes_file(temp_plans_file):
    plan = core.save_plan("Beirut", TOMORROW, "picnic", "bring snacks")
    assert plan.id == 1 and not plan.already_existed
    assert temp_plans_file.exists()
    assert core.list_plans()[0].note == "bring snacks"


def test_save_plan_is_idempotent():
    first = core.save_plan("Beirut", TOMORROW, "picnic")
    again = core.save_plan("beirut", TOMORROW, "picnic")
    assert again.already_existed and again.id == first.id
    assert len(core.list_plans()) == 1


@pytest.mark.parametrize("bad_date", ["31-12-2026", "tomorrow", "2026-13-01", ""])
def test_save_plan_rejects_bad_date_format(bad_date):
    with pytest.raises(core.InvalidInputError, match="YYYY-MM-DD"):
        core.save_plan("Beirut", bad_date, "picnic")


def test_save_plan_rejects_past_date():
    with pytest.raises(core.InvalidInputError, match="in the past"):
        core.save_plan("Beirut", "2020-01-01", "picnic")


def test_save_plan_rejects_unknown_activity():
    with pytest.raises(core.InvalidInputError, match="activity"):
        core.save_plan("Beirut", TOMORROW, "skydiving")


def test_corrupted_plans_file(temp_plans_file):
    temp_plans_file.write_text("{not json", encoding="utf-8")
    with pytest.raises(core.StorageError, match="corrupted"):
        core.save_plan("Beirut", TOMORROW, "picnic")


# --------------------------- ORCHESTRATION: plan_outing --------------------------- #
async def test_plan_outing_picks_best_day_and_saves(fake_api):
    # Day 0: 80% rain (excluded); day 1: 22°C, 10% rain (best for picnic); day 2: 30°C, 15% rain.
    result = await core.plan_outing("Beirut", "picnic", 3)
    assert result.chosen_day.date == TOMORROW
    assert result.saved_plan.date == TOMORROW and result.saved_plan.activity == "picnic"
    assert len(core.list_plans()) == 1  # the Action step really happened


async def test_plan_outing_activity_changes_choice(fake_api):
    result = await core.plan_outing("Beirut", "beach", 3)  # beach prefers ~29°C
    assert result.chosen_day.date == (date.today() + timedelta(days=2)).isoformat()


async def test_plan_outing_no_suitable_day_saves_nothing(fake_api):
    fake_api.daily = make_daily(rain=[90, 85, 70], highs=[20.0, 20.0, 20.0])
    with pytest.raises(core.NoSuitableDayError):
        await core.plan_outing("Beirut", "hiking", 3)
    assert core.list_plans() == []


async def test_plan_outing_propagates_city_not_found(fake_api):
    fake_api.city_found = False
    with pytest.raises(core.CityNotFoundError):
        await core.plan_outing("Atlantisville", "hiking", 3)
    assert core.list_plans() == []
