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

Two ways in for a not-yet-cached Tonie, so this doesn't undo the whole
reason native streaming exists (starting playback without waiting for a
full download first):

- Playing from the beginning (no Range header, or one starting at byte
  0) is streamed live from teddyCloud while simultaneously being written
  to the cache in the background — instant start, forwarding
  teddyCloud's own Content-Length immediately (if it sends one) so
  duration/seekability can be determined right away too, even though the
  body itself is still arriving. Safe: a request for byte 0 never
  exercises teddyCloud's buggy comparison in the first place (that only
  misfires near the *end* of a file).
- A genuine seek into the middle or end of a Tonie that hasn't been
  cached yet at all (e.g. resuming a saved position before anything has
  streamed) can't be served this way without risking the bug, so it
  waits for one full, correctly-cached download instead of forwarding
  the Range to teddyCloud. Uncommon in practice — the common case is
  seeking *within* a Tonie already playing, which is always already
  cached by then.
"""
from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path
from typing import Awaitable, Callable

import aiohttp
from aiohttp import web

_CHUNK_SIZE = 65536
# Bounds disk usage without needing any size-based accounting - a family's
# worth of recently-played Tonies, not a full mirror of the library.
_MAX_ENTRIES = 20


class ContentCache:
    """Downloads teddyCloud content once per key and caches it on local
    disk, evicting least-recently-used entries past a small cap.
    Concurrent requests for the same key share a single download.
    """

    def __init__(self) -> None:
        self._dir = Path(tempfile.mkdtemp(prefix="teddycloud_cache_"))
        self._locks: dict[str, asyncio.Lock] = {}
        self._order: list[str] = []

    def _path(self, key: str) -> Path:
        return self._dir / key

    def is_cached(self, key: str) -> bool:
        return self._path(key).exists()

    async def serve_live_and_cache(
        self,
        key: str,
        fetch: Callable[[], Awaitable],
        request: web.Request,
        base_headers: dict[str, str],
    ) -> web.StreamResponse:
        """Build, prepare and return a StreamResponse for `key`, either
        reading it from a completed cache, waiting for another in-flight
        download of the same key to finish and reading that, or — the
        first caller for this key — fetching it live from `fetch()`,
        forwarding the upstream's own Content-Length (if any) immediately
        so the client learns duration/seekability without waiting for the
        transfer to finish, while writing the same bytes to the cache as
        they arrive."""
        path = self._path(key)

        async def serve_from_file() -> web.StreamResponse:
            self._touch(key)
            headers = dict(base_headers)
            headers["Content-Length"] = str(path.stat().st_size)
            response = web.StreamResponse(status=200, headers=headers)
            await response.prepare(request)
            async for chunk in self._read_file(path):
                await response.write(chunk)
            await response.write_eof()
            return response

        if path.exists():
            return await serve_from_file()

        lock = self._locks.setdefault(key, asyncio.Lock())
        if lock.locked():
            async with lock:
                pass
            return await serve_from_file()

        async with lock:
            if path.exists():
                return await serve_from_file()

            tmp_path = path.with_suffix(".partial")
            response: web.StreamResponse | None = None
            try:
                async with fetch() as upstream:
                    headers = dict(base_headers)
                    content_length = upstream.headers.get("Content-Length")
                    if content_length:
                        headers["Content-Length"] = content_length
                    response = web.StreamResponse(status=200, headers=headers)
                    await response.prepare(request)
                    with open(tmp_path, "wb") as f:
                        async for chunk in upstream.content.iter_chunked(_CHUNK_SIZE):
                            f.write(chunk)
                            await response.write(chunk)
            except (aiohttp.ClientError, TimeoutError):
                tmp_path.unlink(missing_ok=True)
                if response is None:
                    raise
                await response.write_eof()
                return response

            tmp_path.rename(path)
            self._touch(key)
            self._evict_if_needed()
            await response.write_eof()
            return response

    async def ensure_full(self, key: str, fetch: Callable[[], Awaitable]) -> Path:
        """Return the local cached file for `key`, blocking until it's
        fully downloaded first if it isn't cached (or being cached by
        serve_live_and_cache) yet."""
        path = self._path(key)
        if path.exists():
            self._touch(key)
            return path

        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            if not path.exists():
                tmp_path = path.with_suffix(".partial")
                async with fetch() as upstream:
                    with open(tmp_path, "wb") as f:
                        async for chunk in upstream.content.iter_chunked(_CHUNK_SIZE):
                            f.write(chunk)
                tmp_path.rename(path)

        self._touch(key)
        self._evict_if_needed()
        return path

    @staticmethod
    async def _read_file(path: Path):
        with open(path, "rb") as f:
            while True:
                chunk = f.read(_CHUNK_SIZE)
                if not chunk:
                    break
                yield chunk

    def _touch(self, key: str) -> None:
        if key in self._order:
            self._order.remove(key)
        self._order.append(key)

    def _evict_if_needed(self) -> None:
        while len(self._order) > _MAX_ENTRIES:
            oldest = self._order.pop(0)
            self._path(oldest).unlink(missing_ok=True)
            self._locks.pop(oldest, None)

    def close(self) -> None:
        shutil.rmtree(self._dir, ignore_errors=True)


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
