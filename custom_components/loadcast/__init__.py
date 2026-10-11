"""LoadCast – household load forecasting for Home Assistant.

Placeholder skeleton (v0.0.1). The integration currently does nothing; it
exists so the repository has a valid HACS structure while the forecasting
model is being built. See SPEC.md in the repository for the design.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType

DOMAIN = "loadcast"


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up LoadCast.

    Nothing to set up yet. Returning True keeps Home Assistant happy if the
    integration is installed before the first functional release.
    """
    return True
