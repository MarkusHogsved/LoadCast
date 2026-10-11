"""Config flow for LoadCast.

Placeholder flow: a single confirmation step that creates one entry. The real
flow (load source, sub-loads, temperature) replaces this in milestone 2.
"""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from .const import DOMAIN


class LoadCastConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the LoadCast config flow."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm adding LoadCast."""
        # Only one instance for now; multiple homes/meters come later.
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        if user_input is not None:
            return self.async_create_entry(title="LoadCast", data={})

        # Empty form = a plain "Submit" confirmation dialog.
        return self.async_show_form(step_id="user")
