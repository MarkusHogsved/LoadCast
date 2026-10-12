"""LoadCast sensors."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import LoadCastConfigEntry, LoadCastCoordinator


@dataclass(frozen=True, kw_only=True)
class LoadCastSensorDescription(SensorEntityDescription):
    """Describes a LoadCast sensor."""

    value_fn: Callable[[LoadCastCoordinator], float | None]
    attrs_fn: Callable[[LoadCastCoordinator], dict[str, Any]] | None = None


def _next_24h(c: LoadCastCoordinator) -> float:
    now = dt_util.now()
    return c.energy_between(now, now + timedelta(hours=24))


def _remaining_today(c: LoadCastCoordinator) -> float:
    now = dt_util.now()
    return c.energy_between(now, dt_util.start_of_local_day(now) + timedelta(days=1))


def _next_hour(c: LoadCastCoordinator) -> float:
    now = dt_util.now()
    return c.energy_between(now, now + timedelta(hours=1))


def _forecast_attrs(c: LoadCastCoordinator) -> dict[str, Any]:
    """Solcast-style hourly forecast plus per-component detail."""
    data = c.data
    return {
        "forecast": [
            {
                "period_start": h.start.astimezone(
                    dt_util.get_default_time_zone()
                ).isoformat(),
                "kwh": round(h.kwh, 3),
                "kwh_p10": round(h.p10, 3),
                "kwh_p90": round(h.p90, 3),
            }
            for h in data.hours
        ],
        "components": {
            name: {
                "included_in_total": info.included,
                "ready": info.ready,
                "half_life_days": info.half_life_days,
                "uses_temperature": info.uses_temperature,
                "temperature_slope_kwh_per_c": round(info.slope_kwh_per_c, 4),
                "samples": info.samples,
                "changed_since": info.changed_since.isoformat()
                if info.changed_since
                else None,
                "forecast_24h_kwh": round(
                    sum(
                        h.components[name].kwh
                        for h in data.hours[:24]
                        if name in h.components
                    ),
                    2,
                ),
            }
            for name, info in data.components.items()
        },
        "ready": data.ready,
        "fitted_at": data.fitted_at.isoformat() if data.fitted_at else None,
    }


def _accuracy_attrs(c: LoadCastCoordinator) -> dict[str, Any]:
    return {"bias_7d_kwh": c.data.bias_7d_kwh, "days": c.data.error_days}


SENSORS: tuple[LoadCastSensorDescription, ...] = (
    LoadCastSensorDescription(
        key="forecast_24h",
        translation_key="forecast_24h",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=1,
        value_fn=_next_24h,
        attrs_fn=_forecast_attrs,
    ),
    LoadCastSensorDescription(
        key="remaining_today",
        translation_key="remaining_today",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=1,
        value_fn=_remaining_today,
    ),
    LoadCastSensorDescription(
        key="next_hour",
        translation_key="next_hour",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        value_fn=_next_hour,
    ),
    LoadCastSensorDescription(
        key="accuracy_yesterday",
        translation_key="accuracy_yesterday",
        native_unit_of_measurement=PERCENTAGE,
        suggested_display_precision=1,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c: c.data.accuracy_yesterday_pct,
        attrs_fn=_accuracy_attrs,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LoadCastConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up LoadCast sensors."""
    coordinator = entry.runtime_data
    async_add_entities(LoadCastSensor(coordinator, d) for d in SENSORS)


class LoadCastSensor(CoordinatorEntity[LoadCastCoordinator], SensorEntity):
    """A LoadCast forecast sensor."""

    entity_description: LoadCastSensorDescription
    _attr_has_entity_name = True
    # The forecast list is large and changes every update; keep it out of the
    # recorder database.
    _unrecorded_attributes = frozenset({"forecast", "components", "days"})

    def __init__(
        self, coordinator: LoadCastCoordinator, description: LoadCastSensorDescription
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="LoadCast",
            model="Household load forecast",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def available(self) -> bool:
        return (
            super().available
            and self.coordinator.data is not None
            and (
                self.coordinator.data.ready
                or self.entity_description.key == "accuracy_yesterday"
            )
        )

    @property
    def native_value(self) -> float | None:
        return self.entity_description.value_fn(self.coordinator)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.attrs_fn is None or self.coordinator.data is None:
            return None
        return self.entity_description.attrs_fn(self.coordinator)
