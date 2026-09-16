"""HTTP views for the wishlist feature: searching teddyCloud's full
tonies.json catalog to add an item, and managing the wishlist itself.

Unlike the streaming/player views, these keep normal Home Assistant
authentication (requires_auth defaults to True on HomeAssistantView) -
they're only ever called from the authenticated frontend (the card's
own JS, via hass.fetchWithAuth), never by an external playback target
with no HA session of its own.
"""
from __future__ import annotations

from aiohttp import web

from homeassistant.components.http import KEY_HASS, HomeAssistantView
from homeassistant.core import HomeAssistant

from .const import DOMAIN


def _coordinator(request: web.Request, entry_id: str):
    hass: HomeAssistant = request.app[KEY_HASS]
    return hass.data.get(DOMAIN, {}).get(entry_id)


class TeddyCloudCatalogSearchView(HomeAssistantView):
    """Searches teddyCloud's full tonies.json catalog for the wishlist's
    add-a-Tonie search box - see tonies_catalog.py for why this searches
    a local copy instead of teddyCloud's own (18-result-capped) search
    endpoint."""

    url = "/api/teddycloud/catalog_search/{entry_id}"
    name = "api:teddycloud:catalog_search"

    async def get(self, request: web.Request, entry_id: str) -> web.Response:
        coordinator = _coordinator(request, entry_id)
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

    url = "/api/teddycloud/wishlist/{entry_id}"
    name = "api:teddycloud:wishlist"

    async def get(self, request: web.Request, entry_id: str) -> web.Response:
        coordinator = _coordinator(request, entry_id)
        if coordinator is None or coordinator.wishlist is None:
            return web.Response(status=404)
        return web.json_response(coordinator.wishlist.items)

    async def post(self, request: web.Request, entry_id: str) -> web.Response:
        coordinator = _coordinator(request, entry_id)
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

    url = "/api/teddycloud/wishlist/{entry_id}/{model}"
    name = "api:teddycloud:wishlist_item"

    async def delete(self, request: web.Request, entry_id: str, model: str) -> web.Response:
        coordinator = _coordinator(request, entry_id)
        if coordinator is None or coordinator.wishlist is None:
            return web.Response(status=404)
        await coordinator.wishlist.async_remove(model)
        return web.json_response(coordinator.wishlist.items)
