"""The TeddyCloud integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv

from .api import TeddyCloudApiClient, build_base_url
from .const import (
    CONF_GITHUB_BRANCH,
    CONF_GITHUB_CHECK_INTERVAL,
    CONF_GITHUB_PATH,
    CONF_GITHUB_REPO,
    CONF_GITHUB_TOKEN,
    CONF_HOST,
    CONF_PORT,
    CONF_SIDECAR_URL,
    CONF_SSL,
    CONF_VERIFY_SSL,
    DEFAULT_GITHUB_BRANCH,
    DEFAULT_GITHUB_CHECK_INTERVAL,
    DOMAIN,
)
from .content_cache import ContentCache
from .coordinator import TeddyCloudCoordinator
from .github_nfc_source import GitHubNfcSource
from .services import async_register_services
from .player_view import TeddyCloudPlayerView
from .remux_view import TeddyCloudRemuxView
from .sidecar_api import SidecarApiClient
from .stream_view import TeddyCloudStreamView
from .tonies_catalog import ToniesJsonCatalog
from .wishlist import Wishlist
from .wishlist_backup_import import async_import_matching_wishlist_items
from .wishlist_views import (
    TeddyCloudCatalogSearchView,
    TeddyCloudWishlistImportBackupsView,
    TeddyCloudWishlistItemView,
    TeddyCloudWishlistView,
)

_LOGGER = logging.getLogger(__name__)

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
    hass.http.register_view(TeddyCloudCatalogSearchView())
    hass.http.register_view(TeddyCloudWishlistView())
    hass.http.register_view(TeddyCloudWishlistItemView())
    hass.http.register_view(TeddyCloudWishlistImportBackupsView())
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    base_url = build_base_url(entry.data[CONF_HOST], entry.data[CONF_PORT], entry.data[CONF_SSL])
    client = TeddyCloudApiClient(hass, base_url, verify_ssl=entry.data[CONF_VERIFY_SSL])

    sidecar_url = entry.data.get(CONF_SIDECAR_URL)
    sidecar_client = SidecarApiClient(hass, sidecar_url) if sidecar_url else None

    github_repo = entry.data.get(CONF_GITHUB_REPO)
    github_source = (
        GitHubNfcSource(
            hass,
            github_repo,
            entry.data.get(CONF_GITHUB_BRANCH) or DEFAULT_GITHUB_BRANCH,
            entry.data.get(CONF_GITHUB_PATH, ""),
            entry.data.get(CONF_GITHUB_TOKEN) or None,
        )
        if github_repo
        else None
    )

    wishlist = Wishlist(hass, entry.entry_id)
    await wishlist.async_load()
    catalog = ToniesJsonCatalog(client)

    check_interval_minutes = entry.data.get(CONF_GITHUB_CHECK_INTERVAL) or DEFAULT_GITHUB_CHECK_INTERVAL
    coordinator = TeddyCloudCoordinator(
        hass,
        client,
        entry.entry_id,
        sidecar_client,
        wishlist=wishlist,
        catalog=catalog,
        github_source=github_source,
        backup_import_check_interval=check_interval_minutes * 60,
    )
    await coordinator.async_config_entry_first_refresh()

    if github_source is not None and sidecar_client is not None:
        # Best-effort: a wishlist item that already has a backup available
        # should get restored without the user needing to remember to
        # trigger anything after every restart, but GitHub/sidecar trouble
        # must never fail entry setup - the periodic in-coordinator check
        # (coordinator.py's _maybe_import_matching_backups) and the
        # on-demand view (TeddyCloudWishlistImportBackupsView) stay
        # available either way.
        try:
            await async_import_matching_wishlist_items(coordinator)
        except Exception:  # noqa: BLE001 - must never fail entry setup
            _LOGGER.exception("teddycloud: startup NFC backup import check failed")

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unloaded
