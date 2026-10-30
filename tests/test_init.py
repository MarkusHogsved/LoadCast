"""End-to-end: real recorder statistics in, sensors and service out."""

from __future__ import annotations

from datetime import datetime, timedelta
from itertools import accumulate
from zoneinfo import ZoneInfo

from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.recorder import Recorder
from homeassistant.components.recorder.models import StatisticMeanType
from homeassistant.components.recorder.statistics import async_import_statistics
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.loadcast.const import (
    CONF_LOAD_MODE,
    CONF_SUBLOAD_INCLUDE,
    CONF_SUBLOAD_NAME,
    CONF_SUBLOAD_SENSOR,
    CONF_SUBLOADS,
    CONF_TEMPERATURE,
    CONF_TOTAL,
    DOMAIN,
    LOAD_MODE_SENSOR,
)

from .fixtures.loader import load_house

TZ = ZoneInfo("Europe/Stockholm")
NOW = datetime(2026, 10, 11, 15, 30, tzinfo=TZ)

POWER = "sensor.house_load_power"
HP = "sensor.heat_pump_energy"
EV = "sensor.ev_charger_energy"
TEMP = "sensor.outdoor_temperature"


def _import(hass: HomeAssistant) -> None:
    """Import the real house fixture as long-term statistics."""
    data = load_house()
    total, hp, ev = data["total"], data["heatpump"], data["ev"]

    async_import_statistics(
        hass,
        {
            "has_mean": True,
            "mean_type": StatisticMeanType.ARITHMETIC,
            "has_sum": False,
            "name": None,
            "source": "recorder",
            "statistic_id": POWER,
            "unit_class": "power",
            "unit_of_measurement": "W",
        },
        [{"start": s.start, "mean": s.kwh * 1000} for s in total],
    )
    async_import_statistics(
        hass,
        {
            "has_mean": True,
            "mean_type": StatisticMeanType.ARITHMETIC,
            "has_sum": False,
            "name": None,
            "source": "recorder",
            "statistic_id": TEMP,
            "unit_class": "temperature",
            "unit_of_measurement": "°C",
        },
        [{"start": s.start, "mean": s.temp_c} for s in total],
    )
    for stat_id, samples in ((HP, hp), (EV, ev)):
        sums = list(accumulate(s.kwh for s in samples))
        async_import_statistics(
            hass,
            {
                "has_mean": False,
                "mean_type": StatisticMeanType.NONE,
                "has_sum": True,
                "name": None,
                "source": "recorder",
                "statistic_id": stat_id,
                "unit_class": "energy",
                "unit_of_measurement": "kWh",
            },
            [
                {"start": s.start, "sum": total_sum, "state": total_sum}
                for s, total_sum in zip(samples, sums, strict=True)
            ],
        )


@pytest.fixture
async def setup_entry(
    recorder_mock: Recorder, hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> MockConfigEntry:
    await hass.config.async_set_time_zone("Europe/Stockholm")
    freezer.move_to(NOW)
    _import(hass)
    await async_wait_recording_done(hass)

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="LoadCast",
        data={
            CONF_LOAD_MODE: LOAD_MODE_SENSOR,
            CONF_TOTAL: POWER,
            CONF_TEMPERATURE: TEMP,
        },
        options={
            CONF_SUBLOADS: [
                {
                    CONF_SUBLOAD_NAME: "heatpump",
                    CONF_SUBLOAD_SENSOR: HP,
                    CONF_SUBLOAD_INCLUDE: True,
                },
                {
                    CONF_SUBLOAD_NAME: "ev",
                    CONF_SUBLOAD_SENSOR: EV,
                    CONF_SUBLOAD_INCLUDE: False,
                },
            ]
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_sensors_from_real_statistics(hass: HomeAssistant, setup_entry) -> None:
    assert setup_entry.state is ConfigEntryState.LOADED

    state = hass.states.get("sensor.loadcast_forecast_next_24_h")
    assert state is not None
    next_24h = float(state.state)
    # The house uses ~40–55 kWh/day excluding the EV; the forecast must be in
    # that range – and nowhere near the ~70 kWh the old regression planned.
    assert 35 < next_24h < 60

    attrs = state.attributes
    assert attrs["ready"] is True
    assert attrs["uncertain"] is False
    assert attrs["uncertain_components"] == []
    assert len(attrs["forecast"]) == 48
    first = attrs["forecast"][0]
    assert first["kwh_p10"] <= first["kwh"] <= first["kwh_p90"]

    comps = attrs["components"]
    assert set(comps) == {"base", "heatpump", "ev"}
    assert comps["ev"]["included_in_total"] is False
    # Heat pump has been off since 2026-09-23: it must have learned that.
    assert comps["heatpump"]["forecast_24h_kwh"] < 7

    remaining = float(hass.states.get("sensor.loadcast_forecast_rest_of_today").state)
    assert 0 < remaining < next_24h

    acc = hass.states.get("sensor.loadcast_forecast_error_yesterday")
    assert acc is not None and float(acc.state) >= 0
    assert len(acc.attributes["days"]) == 7


async def test_get_forecast_service(hass: HomeAssistant, setup_entry) -> None:
    peak = NOW.replace(hour=19, minute=30)
    resp = await hass.services.async_call(
        DOMAIN,
        "get_forecast",
        {"end": peak.isoformat()},
        blocking=True,
        return_response=True,
    )
    assert resp["ready"] is True
    # Four hours of evening consumption: roughly 6–14 kWh in this house.
    assert 4 < resp["kwh"] < 16

    high = await hass.services.async_call(
        DOMAIN,
        "get_forecast",
        {"end": peak.isoformat(), "percentile": 90},
        blocking=True,
        return_response=True,
    )
    assert high["kwh"] >= resp["kwh"]

    hp_only = await hass.services.async_call(
        DOMAIN,
        "get_forecast",
        {"end": (NOW + timedelta(hours=24)).isoformat(), "components": ["heatpump"]},
        blocking=True,
        return_response=True,
    )
    assert hp_only["kwh"] < 7


async def test_placeholder_entry_fails_with_clear_error(
    recorder_mock: Recorder, hass: HomeAssistant
) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_ERROR


async def test_mark_change_relearns_from_new_level(
    hass: HomeAssistant, setup_entry
) -> None:
    """A marked component forgets its history before the change."""
    since = NOW - timedelta(days=3)
    await hass.services.async_call(
        DOMAIN,
        "mark_change",
        {"component": "heatpump", "since": since.isoformat()},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert setup_entry.state is ConfigEntryState.LOADED
    assert setup_entry.options["change_markers"]["heatpump"] == (
        since.astimezone(ZoneInfo("UTC")).isoformat()
    )

    attrs = hass.states.get("sensor.loadcast_forecast_next_24_h").attributes
    comps = attrs["components"]
    # Three days of hourly data remain for the heat pump, the rest is gone.
    assert 60 <= comps["heatpump"]["samples"] <= 72
    assert comps["heatpump"]["changed_since"] is not None
    assert comps["base"]["changed_since"] is None
    assert comps["base"]["samples"] > 24 * 20
    assert attrs["ready"] is True
    # Three days of new data: relearned for real, no longer uncertain.
    assert attrs["uncertain"] is False
    assert comps["heatpump"]["uncertain"] is False
    assert comps["heatpump"]["new_samples"] == comps["heatpump"]["samples"]
    # Sub-loads in the options are untouched.
    assert len(setup_entry.options["subloads"]) == 2


async def test_mark_change_stays_available_but_uncertain(
    hass: HomeAssistant, setup_entry
) -> None:
    """Right after a change the forecast stays available, flagged uncertain,
    and a marker without a real change barely moves it."""
    before = float(hass.states.get("sensor.loadcast_forecast_next_24_h").state)
    await hass.services.async_call(
        DOMAIN, "mark_change", {"component": "heatpump"}, blocking=True
    )
    await hass.async_block_till_done()
    state = hass.states.get("sensor.loadcast_forecast_next_24_h")
    assert state.state != "unavailable"
    attrs = state.attributes
    assert attrs["ready"] is True
    assert attrs["uncertain"] is True
    assert attrs["uncertain_components"] == ["heatpump"]
    assert attrs["components"]["heatpump"]["uncertain"] is True
    assert attrs["components"]["heatpump"]["new_samples"] == 0
    assert attrs["components"]["base"]["uncertain"] is False
    assert float(state.state) == pytest.approx(before, rel=0.1)
    first = attrs["forecast"][0]
    assert first["kwh_p10"] <= first["kwh"] <= first["kwh_p90"]
    resp = await hass.services.async_call(
        DOMAIN,
        "get_forecast",
        {"end": (NOW + timedelta(hours=4)).isoformat()},
        blocking=True,
        return_response=True,
    )
    assert resp["ready"] is True
    assert resp["uncertain"] is True


async def test_mark_change_rejects_unknown_component_and_future(
    hass: HomeAssistant, setup_entry
) -> None:
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "mark_change", {"component": "pool"}, blocking=True
        )
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "mark_change",
            {"component": "heatpump", "since": (NOW + timedelta(days=1)).isoformat()},
            blocking=True,
        )
    assert "change_markers" not in setup_entry.options


async def test_past_keeps_each_hour_as_issued(
    hass: HomeAssistant, setup_entry, freezer: FrozenDateTimeFactory
) -> None:
    """`past` holds every hour's forecast from when the hour began."""
    coordinator = setup_entry.runtime_data
    attrs = hass.states.get("sensor.loadcast_forecast_next_24_h").attributes
    assert len(attrs["past"]) == 1
    first = attrs["past"][0]
    assert first == attrs["forecast"][0]

    # Later in the same hour: the issued value for this hour stays put
    freezer.move_to(NOW + timedelta(minutes=20))
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    attrs = hass.states.get("sensor.loadcast_forecast_next_24_h").attributes
    assert attrs["past"] == [first]

    # A new hour adds its own forecast, the earlier one is kept as it was
    freezer.move_to(NOW + timedelta(hours=1))
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    attrs = hass.states.get("sensor.loadcast_forecast_next_24_h").attributes
    assert [p["period_start"] for p in attrs["past"]] == [
        first["period_start"],
        attrs["forecast"][0]["period_start"],
    ]
    assert attrs["past"][0] == first

    # Two days on, only the last 48 h remain
    freezer.move_to(NOW + timedelta(hours=50))
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    attrs = hass.states.get("sensor.loadcast_forecast_next_24_h").attributes
    assert len(attrs["past"]) == 2
    assert first not in attrs["past"]
