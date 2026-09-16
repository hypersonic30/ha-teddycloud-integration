"""The TeddyCloud integration."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv

from .api import TeddyCloudApiClient, build_base_url
from .const import CONF_HOST, CONF_PORT, CONF_SIDECAR_URL, CONF_SSL, CONF_VERIFY_SSL, DOMAIN
from .content_cache import ContentCache
from .coordinator import TeddyCloudCoordinator
from .services import async_register_services
from .player_view import TeddyCloudPlayerView
from .remux_view import TeddyCloudRemuxView
from .sidecar_api import SidecarApiClient
from .stream_view import TeddyCloudStreamView

PLATFORMS = [Platform.BINARY_SENSOR, Platform.SENSOR, Platform.SWITCH, Platform.SELECT]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

# Key for the single, integration-wide ContentCache instance inside
# hass.data[DOMAIN] — shared by stream_view.py and remux_view.py, kept
# separate from the per-entry coordinators also stored there.
CONTENT_CACHE_KEY = "_content_cache"


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    async_register_services(hass)
    hass.data.setdefault(DOMAIN, {})[CONTENT_CACHE_KEY] = ContentCache(hass)
    hass.http.register_view(TeddyCloudStreamView())
    hass.http.register_view(TeddyCloudPlayerView())
    hass.http.register_view(TeddyCloudRemuxView())
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    base_url = build_base_url(entry.data[CONF_HOST], entry.data[CONF_PORT], entry.data[CONF_SSL])
    client = TeddyCloudApiClient(hass, base_url, verify_ssl=entry.data[CONF_VERIFY_SSL])

    sidecar_url = entry.data.get(CONF_SIDECAR_URL)
    sidecar_client = SidecarApiClient(hass, sidecar_url) if sidecar_url else None

    coordinator = TeddyCloudCoordinator(hass, client, entry.entry_id, sidecar_client)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unloaded
