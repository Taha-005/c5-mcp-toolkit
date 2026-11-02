"""Shared fixtures: isolate the plans file and replace the network with canned Open-Meteo data."""

from datetime import date, timedelta

import pytest

import weather_core as core


@pytest.fixture(autouse=True)
def temp_plans_file(tmp_path, monkeypatch):
    """Every test writes to its own temporary plans file, never to data/plans.json."""
    path = tmp_path / "plans.json"
    monkeypatch.setenv("PLANS_FILE", str(path))
    return path


def make_daily(rain: list[int], highs: list[float]) -> dict:
    n = len(rain)
    return {
        "time": [(date.today() + timedelta(days=i)).isoformat() for i in range(n)],
        "weather_code": [61 if r > 60 else 1 for r in rain],
        "temperature_2m_max": highs,
        "temperature_2m_min": [h - 8 for h in highs],
        "precipitation_probability_max": rain,
        "wind_speed_10m_max": [12.0] * n,
    }


@pytest.fixture
def fake_api(monkeypatch):
    """Patch the HTTP helper. Set fake_api.daily / fake_api.city_found to shape responses."""

    class Fake:
        city_found = True
        daily = make_daily(rain=[80, 10, 15], highs=[15.0, 22.0, 30.0])
        calls: list[str] = []

        async def get_json(self, url, params):
            self.calls.append(url)
            if url == core.GEOCODE_URL:
                if not self.city_found:
                    return {}  # Open-Meteo omits "results" when nothing matches
                return {"results": [{"name": "Beirut", "country": "Lebanon",
                                     "latitude": 33.89, "longitude": 35.50}]}
            n = params["forecast_days"]
            return {"timezone": "Asia/Beirut", "daily": {k: v[:n] for k, v in self.daily.items()}}

    fake = Fake()
    fake.calls = []
    monkeypatch.setattr(core, "_get_json", fake.get_json)
    return fake
