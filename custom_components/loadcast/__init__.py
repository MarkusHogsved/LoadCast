"""LoadCast – household load forecasting for Home Assistant."""

from __future__ import annotations

from datetime import datetime, timedelta

from homeassistant.const import Platform
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import ConfigEntryError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import dt as dt_util
import voluptuous as vol

from .const import (
    ATTR_COMPONENT,
    ATTR_COMPONENTS,
    ATTR_CONFIG_ENTRY_ID,
    ATTR_END,
    ATTR_PERCENTILE,
    ATTR_SINCE,
    ATTR_START,
    BASE_COMPONENT,
    CONF_CHANGE_MARKERS,
    CONF_LOAD_MODE,
    CONF_SUBLOAD_NAME,
    CONF_SUBLOADS,
    DOMAIN,
    SERVICE_GET_FORECAST,
    SERVICE_MARK_CHANGE,
)
from .coordinator import PAST_STORE_VERSION, LoadCastConfigEntry, LoadCastCoordinator

PLATFORMS = [Platform.SENSOR]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

GET_FORECAST_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_START): cv.datetime,
        vol.Required(ATTR_END): cv.datetime,
        vol.Optional(ATTR_PERCENTILE, default=50): vol.All(
            vol.Coerce(int), vol.In([10, 50, 90])
        ),
        vol.Optional(ATTR_COMPONENTS): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
    }
)

MARK_CHANGE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_COMPONENT): cv.string,
        vol.Optional(ATTR_SINCE): cv.datetime,
        vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
    }
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the get_forecast and mark_change services."""

    def _entry(call: ServiceCall) -> LoadCastConfigEntry:
        entries = hass.config_entries.async_loaded_entries(DOMAIN)
        entry_id = call.data.get(ATTR_CONFIG_ENTRY_ID)
        if entry_id:
            entries = [e for e in entries if e.entry_id == entry_id]
        if not entries:
            raise ServiceValidationError("No loaded LoadCast entry found")
        return entries[0]

    async def _get_forecast(call: ServiceCall) -> ServiceResponse:
        coordinator: LoadCastCoordinator = _entry(call).runtime_data

        start = _as_local(call.data.get(ATTR_START)) or dt_util.now()
        end = _as_local(call.data[ATTR_END])
        components = call.data.get(ATTR_COMPONENTS)
        kwh = coordinator.energy_between(
            start, end, call.data[ATTR_PERCENTILE], components
        )
        return {
            "kwh": kwh,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "percentile": call.data[ATTR_PERCENTILE],
            "ready": bool(coordinator.data and coordinator.data.ready),
            "uncertain": bool(
                coordinator.data and coordinator.data.uncertain_components
            ),
        }

    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_FORECAST,
        _get_forecast,
        schema=GET_FORECAST_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )

    async def _mark_change(call: ServiceCall) -> None:
        """Forget a component's history before a known change.

        The component relearns from the new level only. Until it has a day of
        new data it is forecast from its old shape moved to the level of the
        new hours, with a wider P10/P90 band, and the forecast is flagged
        ``uncertain`` (it stays available and ``ready``).
        """
        entry = _entry(call)
        component = call.data[ATTR_COMPONENT]
        names = [BASE_COMPONENT] + [
            s[CONF_SUBLOAD_NAME] for s in entry.options.get(CONF_SUBLOADS, [])
        ]
        if component not in names:
            raise ServiceValidationError(
                f"Unknown component '{component}'. Choose one of: {', '.join(names)}"
            )
        now = dt_util.now()
        since = _as_local(call.data.get(ATTR_SINCE)) or now
        if since > now + timedelta(minutes=5):
            raise ServiceValidationError("'since' must not be in the future")
        markers = dict(entry.options.get(CONF_CHANGE_MARKERS) or {})
        markers[component] = dt_util.as_utc(since).isoformat()
        # The options update listener reloads the entry, which refits.
        hass.config_entries.async_update_entry(
            entry, options={**entry.options, CONF_CHANGE_MARKERS: markers}
        )

    hass.services.async_register(
        DOMAIN, SERVICE_MARK_CHANGE, _mark_change, schema=MARK_CHANGE_SCHEMA
    )
    return True


def _as_local(value: datetime | None) -> datetime | None:
    """Treat naive datetimes as Home Assistant local time."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=dt_util.get_default_time_zone())
    return value


async def async_setup_entry(hass: HomeAssistant, entry: LoadCastConfigEntry) -> bool:
    """Set up LoadCast from a config entry."""
    if CONF_LOAD_MODE not in entry.data:
        # Entry created by the 0.0.x placeholder flow, which asked for nothing.
        raise ConfigEntryError(
            "This LoadCast entry was created by an early placeholder version. "
            "Delete it and add LoadCast again to configure your sensors."
        )
    coordinator = LoadCastCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_reload(hass: HomeAssistant, entry: LoadCastConfigEntry) -> None:
    """Reload when options (sub-loads, window) change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: LoadCastConfigEntry) -> bool:
    """Unload a config entry."""
    # No delayed save may outlive the entry (it would bring the file back
    # after async_remove_entry has deleted it)
    await entry.runtime_data.async_flush_past()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: LoadCastConfigEntry) -> None:
    """Delete the stored past forecasts with the entry."""
    store = Store(hass, PAST_STORE_VERSION, f"{DOMAIN}.{entry.entry_id}.past")
    await store.async_remove()
