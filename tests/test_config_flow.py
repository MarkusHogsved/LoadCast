"""Config and options flow tests."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.loadcast.const import (
    CONF_BATTERY_IN,
    CONF_BATTERY_OUT,
    CONF_GRID_EXPORT,
    CONF_GRID_IMPORT,
    CONF_LOAD_MODE,
    CONF_SOLAR,
    CONF_SUBLOAD_INCLUDE,
    CONF_SUBLOAD_NAME,
    CONF_SUBLOAD_SENSOR,
    CONF_SUBLOADS,
    CONF_TEMPERATURE,
    CONF_TOTAL,
    DOMAIN,
    LOAD_MODE_COMPUTED,
    LOAD_MODE_SENSOR,
)


async def _start(hass: HomeAssistant, mode: str):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_NAME: "Hemma", CONF_LOAD_MODE: mode}
    )


async def test_sensor_mode_flow(hass: HomeAssistant) -> None:
    result = await _start(hass, LOAD_MODE_SENSOR)
    assert result["step_id"] == "sensor"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_TOTAL: "sensor.house_power"}
    )
    assert result["step_id"] == "temperature"
    with patch("custom_components.loadcast.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_TEMPERATURE: "sensor.outdoor"}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Hemma"
    assert result["data"] == {
        CONF_LOAD_MODE: LOAD_MODE_SENSOR,
        CONF_TOTAL: "sensor.house_power",
        CONF_TEMPERATURE: "sensor.outdoor",
    }


async def test_computed_mode_flow(hass: HomeAssistant) -> None:
    result = await _start(hass, LOAD_MODE_COMPUTED)
    assert result["step_id"] == "computed"
    flows = {
        CONF_GRID_IMPORT: "sensor.imp",
        CONF_GRID_EXPORT: "sensor.exp",
        CONF_SOLAR: "sensor.sol",
        CONF_BATTERY_OUT: "sensor.bout",
        CONF_BATTERY_IN: "sensor.bin",
    }
    result = await hass.config_entries.flow.async_configure(result["flow_id"], flows)
    with patch("custom_components.loadcast.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_LOAD_MODE: LOAD_MODE_COMPUTED, **flows}


async def test_options_add_and_remove_subload(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_LOAD_MODE: LOAD_MODE_SENSOR, CONF_TOTAL: "sensor.house"},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "add_subload"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_SUBLOAD_NAME: "Heat pump",
            CONF_SUBLOAD_SENSOR: "sensor.hp_energy",
            CONF_SUBLOAD_INCLUDE: True,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_SUBLOADS] == [
        {
            CONF_SUBLOAD_NAME: "Heat pump",
            CONF_SUBLOAD_SENSOR: "sensor.hp_energy",
            CONF_SUBLOAD_INCLUDE: True,
        }
    ]

    # Duplicate and reserved names are rejected
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "add_subload"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_SUBLOAD_NAME: "base",
            CONF_SUBLOAD_SENSOR: "sensor.x",
            CONF_SUBLOAD_INCLUDE: True,
        },
    )
    assert result["errors"] == {CONF_SUBLOAD_NAME: "invalid_name"}

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "remove_subload"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SUBLOAD_NAME: "Heat pump"}
    )
    assert entry.options[CONF_SUBLOADS] == []


async def test_options_replace_meter_keeps_history_via_previous_sensor(
    hass: HomeAssistant,
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_LOAD_MODE: LOAD_MODE_SENSOR, CONF_TOTAL: "sensor.house"},
        options={
            CONF_SUBLOADS: [
                {
                    CONF_SUBLOAD_NAME: "Heat pump",
                    CONF_SUBLOAD_SENSOR: "sensor.circuit_energy",
                    CONF_SUBLOAD_INCLUDE: True,
                }
            ],
            "change_markers": {"Heat pump": "2026-10-12T19:45:00+00:00"},
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert "replace_meter" in result["menu_options"]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "replace_meter"}
    )
    # The current meter again is rejected
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_SUBLOAD_NAME: "Heat pump", CONF_SUBLOAD_SENSOR: "sensor.circuit_energy"},
    )
    assert result["errors"] == {CONF_SUBLOAD_SENSOR: "same_sensor"}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_SUBLOAD_NAME: "Heat pump", CONF_SUBLOAD_SENSOR: "sensor.hp_energy"},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_SUBLOADS] == [
        {
            CONF_SUBLOAD_NAME: "Heat pump",
            CONF_SUBLOAD_SENSOR: "sensor.hp_energy",
            CONF_SUBLOAD_INCLUDE: True,
            "previous_sensor": "sensor.circuit_energy",
        }
    ]
    # The change marker stays
    assert entry.options["change_markers"] == {
        "Heat pump": "2026-10-12T19:45:00+00:00"
    }


async def test_options_settings_shows_current_values_and_stores_ints(
    hass: HomeAssistant,
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_LOAD_MODE: LOAD_MODE_SENSOR, CONF_TOTAL: "sensor.house"},
        options={"window_days": 21, "horizon_hours": 24},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "settings"}
    )
    defaults = {str(k): k.default() for k in result["data_schema"].schema}
    assert defaults == {"window_days": 21, "horizon_hours": "24"}

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"window_days": 35.0, "horizon_hours": "48"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["window_days"] == 35
    assert entry.options["horizon_hours"] == 48
