"""Local disk cache for teddyCloud's header-stripped audio content.

Streaming/seeking directly against teddyCloud's own /content/download
endpoint inherits a real bug in its embedded HTTP server: with
skip_header=true (required — the raw TAF file isn't valid Ogg on its
own), the Content-Range/Content-Length it sends are computed from the
client's requested byte offset *before* the TAF header-length adjustment
is applied, but the later decision of whether to actually seek within
the file compares the *already-adjusted* offset against the *unadjusted*
total size (teddyCloud src/cyclone/cyclone_tcp/http/http_server.c,
around lines 1092-1157). For an offset within the last few KB of a file,
that comparison can fail, and teddyCloud silently serves bytes from the
start of the file while its already-sent headers still promise the
requested near-end range — confirmed on real iOS hardware via a
MEDIA_ERR_DECODE at readyState=4 (have-enough-data) / networkState=1
(idle): a *successful* HTTP response, just the wrong bytes.

Sidestepped the way github.com/ndeluigi/teddycloud-companion's own
server does it: fetch the content once and cache it locally, then serve
every Range request ourselves with a correct, from-scratch
implementation — teddyCloud's own Range logic, and its bug, is never
exercised again for that Tonie.

Every download for a given key runs as one background task, independent
of any particular HTTP response - not the earlier design, where the
download for "playing from the start" lived and died with that one
response. That mattered in practice: a listener jumping to a chapter
aborts the original streaming connection (a native <audio> seek cancels
and replaces its in-flight request), and tying the download to that
response meant the whole thing restarted from byte zero on every seek,
which could only ever finish - and therefore only ever answer *any*
pending Range request, even ones well within what had already
downloaded - once the entire file had been re-fetched from scratch.
Now every request for a key, whichever kind, attaches to the one shared
download (starting it if nothing is running or cached yet) and reads
whatever part of the growing file it needs as soon as those bytes exist,
rather than either the whole file or nothing.

Two ways in for a not-yet-cached Tonie, so this doesn't undo the whole
reason native streaming exists (starting playback without waiting for a
full download first):

- Playing from the beginning (no Range header, or one starting at byte
  0) attaches to the shared download from its first byte, forwarding
  teddyCloud's own Content-Length as soon as it's known so duration/
  seekability can be determined right away too, even though the body
  itself is still arriving.
- Seeking to any other offset attaches to the very same shared download
  (starting one if this is the first request for this Tonie at all) and
  waits only until *that offset* has actually downloaded - not the whole
  file - before reading it. Safe with respect to teddyCloud's bug either
  way: teddyCloud's own Range logic is never exercised for any of this,
  only our own file reads against the local, already-correctly-ordered
  copy.

Every filesystem call here runs through hass.async_add_executor_job()
rather than directly in the event loop - confirmed on a real deployment
that skipping this isn't just a style nitpick: Home Assistant's own
blocking-call detector caught a direct open()/write() here, and stream
stutters/failed chapter jumps were observed alongside it. A blocked event
loop during a write can't service a concurrent seek request either,
which plausibly contributed to exactly that.
"""
from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path
from typing import AsyncIterator, Awaitable, BinaryIO, Callable

import aiohttp
from aiohttp import web

from homeassistant.core import HomeAssistant

_CHUNK_SIZE = 65536
# Bounds disk usage without needing any size-based accounting - a family's
# worth of recently-played Tonies, not a full mirror of the library.
_MAX_ENTRIES = 20
# How long to wait for the upstream's Content-Length before starting a
# live response without one - long enough for any real server's headers
# to arrive, short enough not to noticeably delay instant start if it
# never sends one.
_SIZE_WAIT_TIMEOUT = 2.0


class _Download:
    """Tracks one in-progress background download of a single cache key -
    deliberately independent of any particular HTTP response, so a
    listener seeking away from (and thereby disconnecting) whichever
    request originally started it doesn't stop the download itself.
    """

    def __init__(self) -> None:
        self.bytes_written = 0
        self.total_size: int | None = None
        self.error: BaseException | None = None
        self.done = asyncio.Event()
        self.size_known = asyncio.Event()
        self._condition = asyncio.Condition()

    async def wait_until(self, num_bytes: int) -> None:
        """Block until at least `num_bytes` have been written, or the
        download has finished (successfully or not) - whichever first."""
        async with self._condition:
            await self._condition.wait_for(
                lambda: self.done.is_set() or self.bytes_written >= num_bytes
            )

    async def _advance(self, bytes_written: int) -> None:
        async with self._condition:
            self.bytes_written = bytes_written
            self._condition.notify_all()

    def _set_total_size(self, size: int) -> None:
        self.total_size = size
        self.size_known.set()

    async def _finish(self, error: BaseException | None = None) -> None:
        self.error = error
        self.done.set()
        self.size_known.set()
        async with self._condition:
            self._condition.notify_all()


class ContentCache:
    """Downloads teddyCloud content once per key and caches it on local
    disk, evicting least-recently-used entries past a small cap.
    Concurrent requests for the same key, of any kind (playing from the
    start, or seeking anywhere), share a single background download.
    """

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self._dir = Path(tempfile.mkdtemp(prefix="teddycloud_cache_"))
        self._downloads: dict[str, _Download] = {}
        self._order: list[str] = []

    def _path(self, key: str) -> Path:
        return self._dir / key

    def _tmp_path(self, key: str) -> Path:
        return self._path(key).with_suffix(".partial")

    async def _exec(self, func, *args):
        return await self._hass.async_add_executor_job(func, *args)

    async def _exists(self, path: Path) -> bool:
        return await self._exec(path.exists)

    async def is_cached(self, key: str) -> bool:
        return await self._exists(self._path(key))

    def _start_download(self, key: str, fetch: Callable[[], Awaitable]) -> _Download:
        """Return the in-progress download for `key`, starting one as a
        standalone background task if nothing is already running."""
        download = self._downloads.get(key)
        if download is not None:
            return download
        download = _Download()
        self._downloads[key] = download
        self._hass.async_create_background_task(
            self._run_download(key, fetch, download), name=f"teddycloud_cache_download_{key}"
        )
        return download

    async def _run_download(self, key: str, fetch: Callable[[], Awaitable], download: _Download) -> None:
        path = self._path(key)
        tmp_path = self._tmp_path(key)
        try:
            async with fetch() as upstream:
                content_length = upstream.headers.get("Content-Length")
                if content_length:
                    download._set_total_size(int(content_length))
                f = await self._exec(open, tmp_path, "wb")
                try:
                    written = 0
                    async for chunk in upstream.content.iter_chunked(_CHUNK_SIZE):
                        await self._exec(f.write, chunk)
                        await self._exec(f.flush)
                        written += len(chunk)
                        await download._advance(written)
                finally:
                    await self._exec(f.close)
            await self._exec(tmp_path.rename, path)
            self._touch(key)
            await self._evict_if_needed()
            await download._finish()
        except (aiohttp.ClientError, TimeoutError, OSError) as err:
            await self._exec(lambda: tmp_path.unlink(missing_ok=True))
            await download._finish(err)
        finally:
            self._downloads.pop(key, None)

    async def _stream_from(
        self, key: str, download: _Download, start: int, end: int | None
    ) -> AsyncIterator[bytes]:
        """Yield bytes [start, end) of `key`'s content - or, if `end` is
        None, everything from `start` onward until the download finishes
        - reading from the growing (or, by the time we open it, possibly
        already-completed-and-renamed) file backing it."""
        path = self._path(key)
        tmp_path = self._tmp_path(key)
        try:
            f: BinaryIO = await self._exec(open, tmp_path, "rb")
        except FileNotFoundError:
            f = await self._exec(open, path, "rb")
        try:
            await self._exec(f.seek, start)
            sent = start
            while end is None or sent < end:
                # Wait for just the *next chunk's* worth of bytes, not the
                # whole remaining range - waiting for `end` up front here
                # would (and, before this fix, actually did) block on the
                # entire rest of the download even when the requested
                # range starts well before anything still missing.
                to_read = _CHUNK_SIZE if end is None else min(_CHUNK_SIZE, end - sent)
                await download.wait_until(sent + to_read)
                if download.error is not None and download.bytes_written <= sent:
                    raise download.error
                chunk = await self._exec(f.read, to_read)
                if not chunk:
                    if download.done.is_set():
                        break
                    continue
                sent += len(chunk)
                yield chunk
        finally:
            await self._exec(f.close)

    async def serve_live_and_cache(
        self,
        key: str,
        fetch: Callable[[], Awaitable],
        request: web.Request,
        base_headers: dict[str, str],
    ) -> web.StreamResponse:
        """Build, prepare and return a StreamResponse streaming `key`'s
        content from the beginning - reading a completed cache directly,
        or attaching to a (new or already-running) background download
        and forwarding its own reported Content-Length as soon as it's
        known, without waiting for the transfer to finish."""
        path = self._path(key)

        if await self._exists(path):
            self._touch(key)
            size = (await self._exec(path.stat)).st_size
            headers = dict(base_headers)
            headers["Content-Length"] = str(size)
            response = web.StreamResponse(status=200, headers=headers)
            await response.prepare(request)
            async for chunk in self.read_file(path):
                await response.write(chunk)
            await response.write_eof()
            return response

        download = self._start_download(key, fetch)
        try:
            await asyncio.wait_for(download.size_known.wait(), timeout=_SIZE_WAIT_TIMEOUT)
        except TimeoutError:
            pass

        headers = dict(base_headers)
        if download.total_size is not None:
            headers["Content-Length"] = str(download.total_size)
        response = web.StreamResponse(status=200, headers=headers)
        await response.prepare(request)
        try:
            async for chunk in self._stream_from(key, download, start=0, end=None):
                await response.write(chunk)
        except (aiohttp.ClientError, TimeoutError):
            pass  # upstream failed - whatever was already forwarded stands as a partial response
        except (ConnectionResetError, asyncio.CancelledError):
            pass  # client disconnected (e.g. seeking away) - the download itself keeps running independently
        await response.write_eof()
        return response

    async def get_range(self, key: str, fetch: Callable[[], Awaitable], start: int, length: int) -> AsyncIterator[bytes]:
        """Yield bytes [start, start+length) of `key`'s content, starting
        or reusing a background download and waiting only for that range
        to become available - not the whole file."""
        path = self._path(key)
        if await self._exists(path):
            self._touch(key)
            async for chunk in self.read_range(path, start, length):
                yield chunk
            return

        download = self._start_download(key, fetch)
        async for chunk in self._stream_from(key, download, start, start + length):
            yield chunk

    async def get_total_size(self, key: str, fetch: Callable[[], Awaitable]) -> int:
        """Return the total size of `key`'s content, starting/reusing a
        background download and waiting for it to report Content-Length
        (or, failing that, finish completely) if not already known."""
        path = self._path(key)
        if await self._exists(path):
            return (await self._exec(path.stat)).st_size

        download = self._start_download(key, fetch)
        await download.size_known.wait()
        if download.total_size is not None:
            return download.total_size
        if download.error is not None:
            raise download.error
        # Upstream never sent Content-Length but did finish - final size
        # is just the completed file's size.
        return (await self._exec(path.stat)).st_size

    async def ensure_full(self, key: str, fetch: Callable[[], Awaitable]) -> Path:
        """Return the local cached file for `key`, blocking until it's
        fully downloaded first if it isn't cached (or being downloaded)
        yet - for callers like remux_view.py that need the whole file
        rather than a specific range."""
        path = self._path(key)
        if await self._exists(path):
            self._touch(key)
            return path

        download = self._start_download(key, fetch)
        await download.done.wait()
        if download.error is not None:
            raise download.error
        return path

    async def read_file(self, path: Path) -> AsyncIterator[bytes]:
        """Yield the full contents of `path` in chunks."""
        f: BinaryIO = await self._exec(open, path, "rb")
        try:
            while True:
                chunk = await self._exec(f.read, _CHUNK_SIZE)
                if not chunk:
                    break
                yield chunk
        finally:
            await self._exec(f.close)

    async def size_of(self, path: Path) -> int:
        return (await self._exec(path.stat)).st_size

    async def read_range(self, path: Path, start: int, length: int) -> AsyncIterator[bytes]:
        """Yield up to `length` bytes of `path`, starting at byte `start`."""
        f: BinaryIO = await self._exec(open, path, "rb")
        try:
            await self._exec(f.seek, start)
            remaining = length
            while remaining > 0:
                chunk = await self._exec(f.read, min(_CHUNK_SIZE, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk
        finally:
            await self._exec(f.close)

    def _touch(self, key: str) -> None:
        if key in self._order:
            self._order.remove(key)
        self._order.append(key)

    async def _evict_if_needed(self) -> None:
        while len(self._order) > _MAX_ENTRIES:
            oldest = self._order.pop(0)
            await self._exec(lambda: self._path(oldest).unlink(missing_ok=True))

    async def invalidate(self, key: str) -> None:
        """Drop `key`'s cached file, if any.

        Needed when a physical tag's content changes without its ruid
        changing (e.g. assign_nfc_tag reassigning the same tag to
        different audio) - otherwise a listener would keep hearing
        whatever was cached under that ruid until it aged out of the LRU
        or Home Assistant restarted.

        Doesn't cancel a download already in flight for this key (rare in
        practice - it means the tag was reassigned mid-stream) - that
        download still completes and gets cached under the same key
        afterward, since cancelling a task safely from here would need
        more plumbing than this edge case has earned so far.
        """
        if key in self._order:
            self._order.remove(key)
        await self._exec(lambda: self._path(key).unlink(missing_ok=True))

    async def close(self) -> None:
        await self._exec(lambda: shutil.rmtree(self._dir, ignore_errors=True))


def range_start_or_none(range_header: str | None) -> int | None:
    """Cheaply extract just the start offset from a 'bytes=' Range header,
    without needing to know the resource size. Returns 0 if there's no
    Range header at all (equivalent to "from the beginning"), or None for
    anything that isn't a simple 'bytes=N-...' range (e.g. a suffix range
    'bytes=-500', or something unparseable) — callers should treat None
    conservatively, as "not provably safe to stream live from the start"."""
    if not range_header:
        return 0
    units, _, spec = range_header.partition("=")
    if units.strip().lower() != "bytes":
        return 0
    start_s, _, _ = spec.strip().split(",")[0].partition("-")
    if start_s == "":
        return None
    try:
        return int(start_s)
    except ValueError:
        return None


def parse_range(range_header: str | None, size: int) -> tuple[int, int] | None:
    """Parse a single 'bytes=start-end' Range header. Returns (start, end)
    inclusive, or None if the header is absent/not a byte range. Raises
    ValueError for a range that can't be satisfied (caller should
    respond 416)."""
    if not range_header or "=" not in range_header:
        return None
    units, _, spec = range_header.partition("=")
    if units.strip().lower() != "bytes":
        return None
    start_s, _, end_s = spec.strip().split(",")[0].partition("-")
    if start_s == "":
        if not end_s:
            return None
        start, end = max(0, size - int(end_s)), size - 1
    else:
        start = int(start_s)
        end = int(end_s) if end_s else size - 1
    end = min(end, size - 1)
    if start > end or start >= size:
        raise ValueError("unsatisfiable range")
    return start, end
