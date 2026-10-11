"""LoadCast – household load forecasting for Home Assistant.

Skeleton (v0.0.2): the integration can be added from the UI but does not
create any entities yet. See SPEC.md in the repository for the design.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN  # noqa: F401  (re-exported for convenience)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up LoadCast from a config entry.

    Nothing to set up yet – the forecasting coordinator and sensors arrive in
    a later milestone. Returning True marks the entry as loaded.
    """
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry. No platforms are forwarded yet."""
    return True
