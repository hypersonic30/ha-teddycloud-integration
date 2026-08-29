"""The TeddyCloud integration."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .api import TeddyCloudApiClient
from .const import CONF_HOST, CONF_PORT, CONF_SSL, CONF_VERIFY_SSL, DOMAIN
from .coordinator import TeddyCloudCoordinator

PLATFORMS = [Platform.BINARY_SENSOR, Platform.SENSOR, Platform.SWITCH, Platform.SELECT]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    scheme = "https" if entry.data[CONF_SSL] else "http"
    base_url = f"{scheme}://{entry.data[CONF_HOST]}:{entry.data[CONF_PORT]}"

    client = TeddyCloudApiClient(hass, base_url, verify_ssl=entry.data[CONF_VERIFY_SSL])
    coordinator = TeddyCloudCoordinator(hass, client)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unloaded
