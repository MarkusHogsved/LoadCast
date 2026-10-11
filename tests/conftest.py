"""Shared fixtures."""

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(recorder_mock, enable_custom_integrations):
    """Every test gets a recorder (LoadCast depends on it) and may load
    custom_components/loadcast. recorder_mock must come before hass."""
    yield
