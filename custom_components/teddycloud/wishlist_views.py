"""HTTP views for the wishlist feature: searching teddyCloud's full
tonies.json catalog to add an item, and managing the wishlist itself.

Unlike the streaming/player views, these keep normal Home Assistant
authentication (requires_auth defaults to True on HomeAssistantView) -
they're only ever called from the authenticated frontend (the card's
own JS, via hass.fetchWithAuth), never by an external playback target
with no HA session of its own.

Addressed by device_id, not config entry_id: a Lovelace card only has
cheap access to hass.entities, whose entries carry device_id (not
config_entry_id - that field only exists on the full entity registry
entry, which isn't preloaded there). Same device_id -> entry_id
resolution via the device registry that services.py's _resolve_box()
already uses for the assign_nfc_tag service.
"""
from __future__ import annotations

from aiohttp import web

from homeassistant.components.http import KEY_HASS, HomeAssistantView
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN
from .github_nfc_source import GitHubNfcSourceError
from .wishlist_backup_import import async_import_matching_wishlist_items


def _coordinator(request: web.Request, device_id: str):
    hass: HomeAssistant = request.app[KEY_HASS]
    device = dr.async_get(hass).async_get(device_id)
    if device is None:
        return None
    entry_id = next(iter(device.config_entries), None)
    if entry_id is None:
        return None
    return hass.data.get(DOMAIN, {}).get(entry_id)


class TeddyCloudCatalogSearchView(HomeAssistantView):
    """Searches teddyCloud's full tonies.json catalog for the wishlist's
    add-a-Tonie search box - see tonies_catalog.py for why this searches
    a local copy instead of teddyCloud's own (18-result-capped) search
    endpoint."""

    url = "/api/teddycloud/catalog_search/{device_id}"
    name = "api:teddycloud:catalog_search"

    async def get(self, request: web.Request, device_id: str) -> web.Response:
        coordinator = _coordinator(request, device_id)
        if coordinator is None or coordinator.catalog is None:
            return web.Response(status=404)

        query = request.query.get("q", "")
        if not query.strip():
            return web.json_response([])

        try:
            results = await coordinator.catalog.search(query)
        except Exception:  # noqa: BLE001 - catalog fetch failure, e.g. teddyCloud unreachable
            return web.Response(status=502, text="Could not reach the tonies.json catalog")

        return web.json_response(
            [
                {
                    "model": entry.get("model"),
                    "title": entry.get("title"),
                    "series": entry.get("series"),
                    "episodes": entry.get("episodes"),
                    "picture": entry.get("pic") or entry.get("picture"),
                    "language": entry.get("language"),
                }
                for entry in results
            ]
        )


class TeddyCloudWishlistView(HomeAssistantView):
    """Lists the wishlist, and adds an item to it."""

    url = "/api/teddycloud/wishlist/{device_id}"
    name = "api:teddycloud:wishlist"

    async def get(self, request: web.Request, device_id: str) -> web.Response:
        coordinator = _coordinator(request, device_id)
        if coordinator is None or coordinator.wishlist is None:
            return web.Response(status=404)
        return web.json_response(coordinator.wishlist.items)

    async def post(self, request: web.Request, device_id: str) -> web.Response:
        coordinator = _coordinator(request, device_id)
        if coordinator is None or coordinator.wishlist is None:
            return web.Response(status=404)

        try:
            body = await request.json()
        except ValueError:
            return web.Response(status=400, text="Invalid JSON body")

        model = body.get("model")
        title = body.get("title")
        if not model or not title:
            return web.Response(status=400, text="model and title are required")

        await coordinator.wishlist.async_add(model, title, body.get("series"), body.get("picture"))
        return web.json_response(coordinator.wishlist.items)


class TeddyCloudWishlistItemView(HomeAssistantView):
    """Removes one wishlist item."""

    url = "/api/teddycloud/wishlist/{device_id}/{model}"
    name = "api:teddycloud:wishlist_item"

    async def delete(self, request: web.Request, device_id: str, model: str) -> web.Response:
        coordinator = _coordinator(request, device_id)
        if coordinator is None or coordinator.wishlist is None:
            return web.Response(status=404)
        await coordinator.wishlist.async_remove(model)
        return web.json_response(coordinator.wishlist.items)


class TeddyCloudWishlistImportBackupsView(HomeAssistantView):
    """Triggers an immediate check of every un-acquired wishlist item
    against the configured GitHub backup repo (see
    wishlist_backup_import.async_import_matching_wishlist_items),
    importing any filename match right away instead of waiting for the
    next periodic check (coordinator.py's _maybe_import_matching_backups,
    throttled to once every 10 minutes)."""

    url = "/api/teddycloud/wishlist/{device_id}/import_from_backups"
    name = "api:teddycloud:wishlist_import_from_backups"

    async def post(self, request: web.Request, device_id: str) -> web.Response:
        coordinator = _coordinator(request, device_id)
        if coordinator is None or coordinator.wishlist is None:
            return web.Response(status=404)
        if coordinator.github_source is None:
            return web.json_response({"error": "no_github_source"}, status=404)
        if coordinator.sidecar_client is None:
            return web.json_response({"error": "no_sidecar"}, status=404)

        try:
            attempted = await async_import_matching_wishlist_items(coordinator)
        except GitHubNfcSourceError as err:
            return web.Response(status=502, text=str(err))

        if attempted:
            await coordinator.async_request_refresh()
        return web.json_response({"attempted": attempted})
