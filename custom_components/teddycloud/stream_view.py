"""HTTP view proxying teddyCloud's audio stream with a corrected Content-Type.

teddyCloud's /content/download endpoint sends Content-Type: application/
octet-stream — its response headers are guessed from the URL's file
extension, and this URL has none. That's fine for in-page playback, where
the ha-teddycloud-card supplies an explicit <source type="audio/ogg"> hint
the browser trusts instead of the server's header, but it's useless to
anything that fetches the URL on its own without that hint — e.g. an
AirPlay receiver independently resolving the stream itself, which then
has nothing to tell it this is Ogg/Opus audio and fails to play it. This
view re-serves the same bytes with the header fixed, for playback targets
that can reach teddyCloud's network directly (i.e. the same LAN — AirPlay
receivers on a different network couldn't reach teddyCloud either way).
"""
from __future__ import annotations

import logging

import aiohttp
from aiohttp import web

from homeassistant.components.http import KEY_HASS, HomeAssistantView
from homeassistant.core import HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

_HEX_CHARS = set("0123456789abcdefABCDEF")


class TeddyCloudStreamView(HomeAssistantView):
    """Proxies teddyCloud's own content stream with Content-Type: audio/ogg."""

    url = "/api/teddycloud/stream/{entry_id}/{overlay}/{ruid}"
    name = "api:teddycloud:stream"
    # No HA login required: the device fetching this (e.g. an AirPlay
    # receiver) has no HA session of its own — same trust model as
    # teddyCloud's own (also unauthenticated) download endpoint this proxies.
    requires_auth = False

    async def get(
        self, request: web.Request, entry_id: str, overlay: str, ruid: str
    ) -> web.StreamResponse:
        hass: HomeAssistant = request.app[KEY_HASS]
        coordinator = hass.data.get(DOMAIN, {}).get(entry_id)
        if coordinator is None:
            return web.Response(status=404)

        if len(ruid) != 16 or not all(c in _HEX_CHARS for c in ruid):
            return web.Response(status=400, text="Invalid ruid")

        try:
            async with coordinator.client.open_content_stream(
                ruid, overlay, request.headers.get("Range")
            ) as upstream:
                response = web.StreamResponse(
                    status=upstream.status,
                    headers={"Content-Type": "audio/ogg", "Accept-Ranges": "bytes"},
                )
                for header in ("Content-Length", "Content-Range"):
                    if header in upstream.headers:
                        response.headers[header] = upstream.headers[header]

                await response.prepare(request)
                async for chunk in upstream.content.iter_chunked(65536):
                    await response.write(chunk)
                await response.write_eof()
                return response
        except (aiohttp.ClientError, TimeoutError) as err:
            _LOGGER.debug("teddycloud: stream proxy failed for ruid %s: %s", ruid, err)
            return web.Response(status=502)
