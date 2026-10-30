"""Config and options flow for LoadCast."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers import selector
import voluptuous as vol

from .const import (
    BASE_COMPONENT,
    CONF_BATTERY_IN,
    CONF_BATTERY_OUT,
    CONF_GRID_EXPORT,
    CONF_GRID_IMPORT,
    CONF_HORIZON_HOURS,
    CONF_LOAD_MODE,
    CONF_SOLAR,
    CONF_SUBLOAD_INCLUDE,
    CONF_SUBLOAD_NAME,
    CONF_SUBLOAD_PREVIOUS,
    CONF_SUBLOAD_SENSOR,
    CONF_SUBLOADS,
    CONF_TEMPERATURE,
    CONF_TOTAL,
    CONF_WEATHER,
    CONF_WINDOW_DAYS,
    DEFAULT_HORIZON_HOURS,
    DEFAULT_WINDOW_DAYS,
    DOMAIN,
    LOAD_MODE_COMPUTED,
    LOAD_MODE_SENSOR,
)

ENERGY_SENSOR = selector.EntitySelector(
    selector.EntitySelectorConfig(
        domain="sensor", device_class=SensorDeviceClass.ENERGY
    )
)
ENERGY_OR_POWER_SENSOR = selector.EntitySelector(
    selector.EntitySelectorConfig(
        domain="sensor",
        device_class=[SensorDeviceClass.ENERGY, SensorDeviceClass.POWER],
    )
)
TEMP_SENSOR = selector.EntitySelector(
    selector.EntitySelectorConfig(
        domain="sensor", device_class=SensorDeviceClass.TEMPERATURE
    )
)
WEATHER = selector.EntitySelector(selector.EntitySelectorConfig(domain="weather"))


class LoadCastConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the LoadCast config flow."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return LoadCastOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Name and how total household load is measured."""
        if user_input is not None:
            self._data.update(user_input)
            if user_input[CONF_LOAD_MODE] == LOAD_MODE_COMPUTED:
                return await self.async_step_computed()
            return await self.async_step_sensor()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_NAME, default="LoadCast"): str,
                    vol.Required(
                        CONF_LOAD_MODE, default=LOAD_MODE_SENSOR
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[LOAD_MODE_SENSOR, LOAD_MODE_COMPUTED],
                            translation_key="load_mode",
                            mode=selector.SelectSelectorMode.LIST,
                        )
                    ),
                }
            ),
        )

    async def async_step_sensor(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """A single sensor measuring total household consumption."""
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_temperature()
        return self.async_show_form(
            step_id="sensor",
            data_schema=vol.Schema({vol.Required(CONF_TOTAL): ENERGY_OR_POWER_SENSOR}),
        )

    async def async_step_computed(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Load = import − export + solar + battery out − battery in."""
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_temperature()
        return self.async_show_form(
            step_id="computed",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_GRID_IMPORT): ENERGY_SENSOR,
                    vol.Optional(CONF_GRID_EXPORT): ENERGY_SENSOR,
                    vol.Optional(CONF_SOLAR): ENERGY_SENSOR,
                    vol.Optional(CONF_BATTERY_OUT): ENERGY_SENSOR,
                    vol.Optional(CONF_BATTERY_IN): ENERGY_SENSOR,
                }
            ),
        )

    async def async_step_temperature(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Optional outdoor temperature (history) and weather forecast."""
        if user_input is not None:
            self._data.update(user_input)
            name = self._data.pop(CONF_NAME)
            return self.async_create_entry(title=name, data=self._data)
        return self.async_show_form(
            step_id="temperature",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_TEMPERATURE): TEMP_SENSOR,
                    vol.Optional(CONF_WEATHER): WEATHER,
                }
            ),
        )


class LoadCastOptionsFlow(OptionsFlow):
    """Sub-loads and model settings."""

    def _subloads(self) -> list[dict[str, Any]]:
        return list(self.config_entry.options.get(CONF_SUBLOADS, []))

    def _save(self, **changes: Any) -> ConfigFlowResult:
        return self.async_create_entry(data={**self.config_entry.options, **changes})

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        menu = ["add_subload", "settings"]
        if self._subloads():
            menu[1:1] = ["replace_meter", "remove_subload"]
        return self.async_show_menu(step_id="init", menu_options=menu)

    async def async_step_add_subload(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add a separately metered load (heat pump, EV charger, pool …)."""
        errors: dict[str, str] = {}
        if user_input is not None:
            name = user_input[CONF_SUBLOAD_NAME].strip()
            existing = {s[CONF_SUBLOAD_NAME] for s in self._subloads()}
            if not name or name == BASE_COMPONENT or name in existing:
                errors[CONF_SUBLOAD_NAME] = "invalid_name"
            else:
                subloads = [
                    *self._subloads(),
                    {
                        CONF_SUBLOAD_NAME: name,
                        CONF_SUBLOAD_SENSOR: user_input[CONF_SUBLOAD_SENSOR],
                        CONF_SUBLOAD_INCLUDE: user_input[CONF_SUBLOAD_INCLUDE],
                    },
                ]
                return self._save(**{CONF_SUBLOADS: subloads})

        return self.async_show_form(
            step_id="add_subload",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SUBLOAD_NAME): str,
                    vol.Required(CONF_SUBLOAD_SENSOR): ENERGY_SENSOR,
                    vol.Required(CONF_SUBLOAD_INCLUDE, default=True): bool,
                }
            ),
            errors=errors,
        )

    async def async_step_replace_meter(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Move a sub-load to a new meter without losing its history.

        The old meter becomes `previous_sensor` and covers the hours before
        the new one's first full hour. Name, total flag and change marker stay.
        """
        errors: dict[str, str] = {}
        if user_input is not None:
            name = user_input[CONF_SUBLOAD_NAME]
            new = user_input[CONF_SUBLOAD_SENSOR]
            current = next(
                s for s in self._subloads() if s[CONF_SUBLOAD_NAME] == name
            )
            if new == current[CONF_SUBLOAD_SENSOR]:
                errors[CONF_SUBLOAD_SENSOR] = "same_sensor"
            else:
                subloads = [
                    {
                        **s,
                        CONF_SUBLOAD_SENSOR: new,
                        CONF_SUBLOAD_PREVIOUS: s[CONF_SUBLOAD_SENSOR],
                    }
                    if s[CONF_SUBLOAD_NAME] == name
                    else s
                    for s in self._subloads()
                ]
                return self._save(**{CONF_SUBLOADS: subloads})
        return self.async_show_form(
            step_id="replace_meter",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SUBLOAD_NAME): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[s[CONF_SUBLOAD_NAME] for s in self._subloads()]
                        )
                    ),
                    vol.Required(CONF_SUBLOAD_SENSOR): ENERGY_SENSOR,
                }
            ),
            errors=errors,
        )

    async def async_step_remove_subload(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            keep = [
                s
                for s in self._subloads()
                if s[CONF_SUBLOAD_NAME] != user_input[CONF_SUBLOAD_NAME]
            ]
            return self._save(**{CONF_SUBLOADS: keep})
        return self.async_show_form(
            step_id="remove_subload",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SUBLOAD_NAME): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[s[CONF_SUBLOAD_NAME] for s in self._subloads()]
                        )
                    )
                }
            ),
        )

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """History window and forecast horizon.

        Explicit selectors so the form shows the current value: a number box
        with unit, and a pre-selected horizon. Select values come back as
        strings and are stored as ints.
        """
        opts = self.config_entry.options
        if user_input is not None:
            return self._save(
                **{
                    CONF_WINDOW_DAYS: int(user_input[CONF_WINDOW_DAYS]),
                    CONF_HORIZON_HOURS: int(user_input[CONF_HORIZON_HOURS]),
                }
            )
        window = int(opts.get(CONF_WINDOW_DAYS, DEFAULT_WINDOW_DAYS))
        horizon = int(opts.get(CONF_HORIZON_HOURS, DEFAULT_HORIZON_HOURS))
        return self.async_show_form(
            step_id="settings",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_WINDOW_DAYS, default=window
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=7,
                            max=90,
                            step=1,
                            unit_of_measurement="d",
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Required(
                        CONF_HORIZON_HOURS, default=str(horizon)
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=["24", "48"],
                            mode=selector.SelectSelectorMode.LIST,
                            translation_key="horizon",
                        )
                    ),
                }
            ),
        )
