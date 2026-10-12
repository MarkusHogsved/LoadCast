"""Constants for LoadCast."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "loadcast"

# Config entry data (set in the config flow)
CONF_LOAD_MODE = "load_mode"
LOAD_MODE_SENSOR = "sensor"
LOAD_MODE_COMPUTED = "computed"

CONF_TOTAL = "total_sensor"  # energy (kWh) or power (W) sensor
CONF_GRID_IMPORT = "grid_import"
CONF_GRID_EXPORT = "grid_export"
CONF_SOLAR = "solar"
CONF_BATTERY_IN = "battery_in"
CONF_BATTERY_OUT = "battery_out"
CONF_TEMPERATURE = "temperature_sensor"
CONF_WEATHER = "weather_entity"

COMPUTED_KEYS = (
    CONF_GRID_IMPORT,
    CONF_GRID_EXPORT,
    CONF_SOLAR,
    CONF_BATTERY_IN,
    CONF_BATTERY_OUT,
)
# Sign of each computed term: load = import − export + solar + out − in
COMPUTED_SIGNS = {
    CONF_GRID_IMPORT: 1,
    CONF_GRID_EXPORT: -1,
    CONF_SOLAR: 1,
    CONF_BATTERY_OUT: 1,
    CONF_BATTERY_IN: -1,
}

# Options (options flow)
CONF_SUBLOADS = "subloads"  # list of {name, sensor, include_in_total}
CONF_SUBLOAD_NAME = "name"
CONF_SUBLOAD_SENSOR = "sensor"
CONF_SUBLOAD_INCLUDE = "include_in_total"
CONF_WINDOW_DAYS = "window_days"
CONF_HORIZON_HOURS = "horizon_hours"
# {component name: ISO timestamp} – history before it is ignored for that
# component (set by the mark_change service after a known change, such as a
# repaired heat pump).
CONF_CHANGE_MARKERS = "change_markers"

DEFAULT_WINDOW_DAYS = 28
DEFAULT_HORIZON_HOURS = 48

BASE_COMPONENT = "base"

UPDATE_INTERVAL = timedelta(minutes=15)

SERVICE_GET_FORECAST = "get_forecast"
ATTR_START = "start"
ATTR_END = "end"
ATTR_PERCENTILE = "percentile"
ATTR_COMPONENTS = "components"
ATTR_CONFIG_ENTRY_ID = "config_entry_id"

SERVICE_MARK_CHANGE = "mark_change"
ATTR_COMPONENT = "component"
ATTR_SINCE = "since"
