"""In-memory cache of teddyCloud's full tonies.json catalog, with simple
substring search — the data source for the wishlist feature (browsing
and searching Tonies not yet owned).

Deliberately not using teddyCloud's own /api/toniesJsonSearch: that
endpoint is built for the "assign a tag" autocomplete elsewhere in
teddyCloud's UI, and is hard-capped to 18 results with a narrower field
set (no title/category/release - just model/series/episode/picture/
language/tracks). Fetching the full, unfiltered /api/toniesJson once and
searching it ourselves avoids both limits, at the cost of holding a few
thousand entries (a few MB of JSON) in memory per config entry.
"""
from __future__ import annotations

import logging
import time

from .api import TeddyCloudApiClient, TeddyCloudApiError
from .text_match import normalize_for_match

_LOGGER = logging.getLogger(__name__)

# tonies.json changes rarely (new official Tonies release occasionally,
# not continuously) - a day-scale refresh is plenty, and avoids re-
# fetching several MB on every search.
_REFRESH_INTERVAL = 24 * 60 * 60

_SEARCH_FIELDS = ("title", "series", "episodes")


class ToniesJsonCatalog:
    """Fetches and caches teddyCloud's tonies.json catalog for one config entry."""

    def __init__(self, client: TeddyCloudApiClient) -> None:
        self._client = client
        self._entries: list[dict] = []
        self._last_fetch: float = 0.0

    async def _ensure_fresh(self) -> None:
        if self._entries and (time.monotonic() - self._last_fetch) < _REFRESH_INTERVAL:
            return
        try:
            entries = await self._client.get_tonies_json_catalog()
        except TeddyCloudApiError as err:
            _LOGGER.debug("teddycloud: tonies.json catalog refresh failed: %s", err)
            if not self._entries:
                raise
            return
        if entries:
            self._entries = entries
            self._last_fetch = time.monotonic()

    async def search(self, query: str, limit: int = 30) -> list[dict]:
        """Accent/case/separator-insensitive substring search across
        title/series/episode - see text_match.py. Catches e.g. a search
        for "Pokemon" against a catalog title of "Pokémon"."""
        await self._ensure_fresh()
        q = normalize_for_match(query)
        if not q:
            return []
        results = []
        for entry in self._entries:
            haystack = normalize_for_match(
                " ".join(str(entry.get(field) or "") for field in _SEARCH_FIELDS)
            )
            if q in haystack:
                results.append(entry)
                if len(results) >= limit:
                    break
        return results

    async def get_by_model(self, model: str) -> dict | None:
        await self._ensure_fresh()
        return next((entry for entry in self._entries if entry.get("model") == model), None)
