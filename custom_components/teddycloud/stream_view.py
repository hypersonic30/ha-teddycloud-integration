"""HTTP view serving teddyCloud's audio content with a corrected
Content-Type and this integration's own local-cache-backed Range
handling.

teddyCloud's /content/download endpoint sends Content-Type: application/
octet-stream — its response headers are guessed from the URL's file
extension, and this URL has none. That's fine for in-page playback, where
the ha-teddycloud-card supplies an explicit <source type="audio/ogg">
hint the browser trusts instead of the server's header, but it's useless
to anything that fetches the URL on its own without that hint — e.g. an
AirPlay receiver independently resolving the stream itself, which then
has nothing to tell it this is Ogg/Opus audio and fails to play it.

Range requests are served from a local cache (content_cache.py) rather
than forwarded to teddyCloud's own endpoint: its embedded HTTP server has
a real bug where a seek within the last few KB of a file can silently
return the wrong bytes while still claiming the requested range in its
(already-sent) response headers — confirmed on real iOS hardware via a
MEDIA_ERR_DECODE. Playing from the start and seeking both attach to the
same shared background download for a given Tonie (content_cache.py
starts one if nothing is running or cached yet): playback from byte 0
gets teddyCloud's own Content-Length as soon as it's known, without
waiting for the transfer to finish, and a seek anywhere else waits only
until *that specific offset* has actually downloaded - not the whole
file, and not forwarded to teddyCloud's buggy Range logic either way.

This matters because a seek aborts and replaces the in-flight connection
a native <audio> element was using (confirmed - that's just how seeking
works) — tying a download to one response's lifetime, an earlier version
of this file did, meant every seek restarted the whole download from
byte zero, and any pending request (including the seek itself) could
only ever be answered once that whole re-download finished. Decoupling
the download from any one response fixes that.
"""
from __future__ import annotations

import logging

import aiohttp
from aiohttp import web

from homeassistant.components.http import KEY_HASS, HomeAssistantView
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .content_cache import parse_range, range_start_or_none

_LOGGER = logging.getLogger(__name__)

_HEX_CHARS = set("0123456789abcdefABCDEF")


class TeddyCloudStreamView(HomeAssistantView):
    """Serves teddyCloud's audio content with correct, cache-backed Range support."""

    url = "/api/teddycloud/stream/{entry_id}/{overlay}/{ruid}"
    name = "api:teddycloud:stream"
    # No HA login required: the device fetching this (e.g. an AirPlay
    # receiver, or the player page's own <audio> element) has no HA
    # session of its own — same trust model as teddyCloud's own (also
    # unauthenticated) download endpoint this is ultimately sourced from.
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

        cache = hass.data[DOMAIN]["_content_cache"]
        key = f"{entry_id}_{overlay}_{ruid}"

        def fetch():
            return coordinator.client.open_content_stream(ruid, overlay)

        range_header = request.headers.get("Range")
        start = range_start_or_none(range_header)

        if not await cache.is_cached(key) and start == 0:
            base_headers = {"Content-Type": "audio/ogg", "Accept-Ranges": "bytes"}
            try:
                return await cache.serve_live_and_cache(key, fetch, request, base_headers)
            except (aiohttp.ClientError, TimeoutError) as err:
                _LOGGER.debug("teddycloud: live stream/cache failed for ruid %s: %s", ruid, err)
                return web.Response(status=502)

        try:
            size = await cache.get_total_size(key, fetch)
        except (aiohttp.ClientError, TimeoutError) as err:
            _LOGGER.debug("teddycloud: stream cache fetch failed for ruid %s: %s", ruid, err)
            return web.Response(status=502)

        try:
            rng = parse_range(range_header, size)
        except ValueError:
            return web.Response(status=416, headers={"Content-Range": f"bytes */{size}"})

        if rng is None:
            start, end, status = 0, size - 1, 200
        else:
            start, end, status = rng[0], rng[1], 206

        length = end - start + 1
        headers = {
            "Content-Type": "audio/ogg",
            "Accept-Ranges": "bytes",
            "Content-Length": str(length),
        }
        if status == 206:
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"

        response = web.StreamResponse(status=status, headers=headers)
        await response.prepare(request)
        try:
            async for chunk in cache.get_range(key, fetch, start, length):
                await response.write(chunk)
        except (aiohttp.ClientError, TimeoutError):
            pass  # upstream failed - whatever was already forwarded stands as a partial response
        await response.write_eof()
        return response
