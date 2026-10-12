"""The Hyresbostäder Utility integration."""

from __future__ import annotations

from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import HyresbostaderAPI
from .const import DOMAIN
from .coordinator import HyresbostaderConfigEntry, HyresbostaderCoordinator

PLATFORMS = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: HyresbostaderConfigEntry) -> bool:
    """Set up Hyresbostäder Utility from a config entry."""
    # Own cookie jar, since the portal login is cookie based.
    session = async_create_clientsession(hass)
    api = HyresbostaderAPI(session, entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD])

    coordinator = HyresbostaderCoordinator(hass, entry, api)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: HyresbostaderConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: HyresbostaderConfigEntry, device_entry: dr.DeviceEntry
) -> bool:
    """Allow deleting a device whose meter is no longer on the portal."""
    return not any(
        domain == DOMAIN and key in entry.runtime_data.data
        for domain, key in device_entry.identifiers
    )
