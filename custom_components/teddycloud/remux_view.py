"""HTTP view that remuxes teddyCloud's Ogg/Opus stream into fragmented MP4.

Only for the player page's instant-start experiment: Media Source
Extensions (MSE) needs to receive data as a supported container/codec
combination, and Safari never accepts a raw Ogg container via
SourceBuffer.appendBuffer() even though it can play the exact same Opus
audio directly via a plain <audio src>. Fragmented MP4 with Opus is the
combination Safari 18.4+ actually supports for MSE.

This is a *remux*, not a transcode: ffmpeg is told `-c:a copy`, so it only
repackages the same compressed Opus frames into a different container —
no decoding, no re-encoding, no quality loss, and cheap on CPU (unlike a
real transcode to e.g. AAC). Verified locally against a real ffmpeg
binary before writing this: `-movflags
frag_keyframe+empty_moov+default_base_moof -frag_duration 2000000`
produces a properly fragmented file (one moof/mdat pair roughly every 2
seconds) rather than a single fragment for the whole file, which matters
here — a single fragment would mean the client has to wait for the
*entire* remux to finish before it can play anything, defeating the
point of this endpoint.

teddyCloud's stream is piped into ffmpeg's stdin and its stdout piped
back to the HTTP client concurrently (both directions have to be pumped
at once — feeding stdin without draining stdout risks ffmpeg's output
buffer filling up and blocking, which would stall the input side too).
"""
from __future__ import annotations

import asyncio
import logging

import aiohttp
from aiohttp import web

from homeassistant.components.ffmpeg import get_ffmpeg_manager
from homeassistant.components.http import KEY_HASS, HomeAssistantView
from homeassistant.core import HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

_HEX_CHARS = set("0123456789abcdefABCDEF")

_FFMPEG_ARGS = (
    "-i", "pipe:0",
    "-c:a", "copy",
    "-f", "mp4",
    "-movflags", "frag_keyframe+empty_moov+default_base_moof",
    "-frag_duration", "2000000",
    "pipe:1",
)


class TeddyCloudRemuxView(HomeAssistantView):
    """Streams a fragmented-MP4 remux of teddyCloud's content for MSE."""

    url = "/api/teddycloud/remux/{entry_id}/{overlay}/{ruid}"
    name = "api:teddycloud:remux"
    # Same reasoning as TeddyCloudStreamView: fetched by the player page's
    # own JS, which carries no HA session either way.
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

        ffmpeg_binary = get_ffmpeg_manager(hass).binary

        try:
            proc = await asyncio.create_subprocess_exec(
                ffmpeg_binary,
                *_FFMPEG_ARGS,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except OSError as err:
            _LOGGER.error("teddycloud: could not start ffmpeg (%s): %s", ffmpeg_binary, err)
            return web.Response(status=501, text="ffmpeg is not available")

        async def feed_stdin() -> None:
            try:
                async with coordinator.client.open_content_stream(
                    ruid, overlay, None
                ) as upstream:
                    async for chunk in upstream.content.iter_chunked(65536):
                        proc.stdin.write(chunk)
                        await proc.stdin.drain()
            except (aiohttp.ClientError, ConnectionError, TimeoutError) as err:
                _LOGGER.debug("teddycloud: remux input feed failed for %s: %s", ruid, err)
            finally:
                if not proc.stdin.is_closing():
                    proc.stdin.close()

        feed_task = asyncio.ensure_future(feed_stdin())

        response = web.StreamResponse(status=200, headers={"Content-Type": "audio/mp4"})
        await response.prepare(request)
        try:
            while True:
                chunk = await proc.stdout.read(65536)
                if not chunk:
                    break
                await response.write(chunk)
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            feed_task.cancel()
            if proc.returncode is None:
                proc.kill()
            await proc.wait()

        await response.write_eof()
        return response
