"""Data update coordinator for the TeddyCloud integration."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from datetime import timedelta
import logging
import time

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import TeddyCloudApiClient, TeddyCloudApiError
from .const import (
    DEFAULT_GITHUB_CHECK_INTERVAL,
    DOMAIN,
    SETTING_IP,
    SETTING_LAST_CONNECTION,
    SETTING_LAST_RUID,
    SETTING_ONLINE,
    UPDATE_INTERVAL,
)
from .github_nfc_source import GitHubNfcSource
from .sidecar_api import SidecarApiClient
from .tonies_catalog import ToniesJsonCatalog
from .wishlist import Wishlist
from .wishlist_backup_import import async_import_matching_wishlist_items

_LOGGER = logging.getLogger(__name__)


@dataclass
class TeddyCloudBoxData:
    """Snapshot of one box's state for a single coordinator refresh."""

    box: dict
    settings: dict
    online: bool
    last_connection: int | None
    ip: str
    last_ruid: str
    tonie_info: dict | None
    # Cached, playable tags for this box — see _build_library().
    library: list[dict] = field(default_factory=list)
    # False when this snapshot is carried over from a previous refresh
    # because this box's fetch failed this round — lets entities report
    # unavailable instead of silently showing stale data forever.
    available: bool = True


def _parse_bool(text: str) -> bool:
    return text.strip().lower() == "true"


def _parse_int(text: str) -> int | None:
    text = text.strip()
    return int(text) if text.lstrip("-").isdigit() else None


def _build_library(tags: list[dict], entry_id: str, box_id: str) -> list[dict]:
    """Cached, playable tags only — skips system tags and ones still downloading.

    audio_url points at this integration's own stream_view proxy rather
    than straight at teddyCloud — a relative path, so the browser resolves
    it against whatever host it's already using to reach HA (no guessing
    HA's own URL server-side), and it fixes teddyCloud's generic
    Content-Type so playback targets that fetch it independently (e.g. an
    AirPlay receiver) know it's Ogg/Opus audio too.
    """
    library = []
    for tag in tags:
        if tag.get("type") != "tag" or not tag.get("valid") or not tag.get("exists"):
            continue
        ruid = tag.get("ruid")
        if not ruid:
            continue
        info = tag.get("tonieInfo") or {}
        title = info.get("episode") or info.get("series") or ruid
        # Per-track start offsets (seconds), straight from teddyCloud's own
        # Ogg granule-position parsing of the actual audio file. Paired up
        # with track titles when available - a *separate*, independent
        # source: teddyCloud's community tonies.json catalog, keyed by
        # model ID, so it only exists for recognized official Tonies and
        # can disagree in count with the real track offsets (a custom/
        # unrecognized Tonie has no titles at all; zip() stops at the
        # shorter list, leaving any extra chapters titled None).
        starts = sorted(s for s in (tag.get("trackSeconds") or []) if isinstance(s, (int, float)))
        raw_titles = info.get("tracks")
        titles = raw_titles if isinstance(raw_titles, list) else []
        chapters = [
            {
                "start": start,
                "title": titles[i] if i < len(titles) and isinstance(titles[i], str) and titles[i] else None,
            }
            for i, start in enumerate(starts)
        ]
        library.append(
            {
                "ruid": ruid,
                "title": title,
                "series": info.get("series") or None,
                "picture": info.get("picture"),
                "chapters": chapters,
                # tonies.json's own identifier for this Tonie, when
                # recognized (None for a custom/unrecognized one) - lets
                # the wishlist (wishlist.py) match "do we now own this"
                # by a stable ID instead of fuzzy title comparison.
                "model": info.get("model") or None,
                "audio_url": f"/api/teddycloud/stream/{entry_id}/{box_id}/{ruid}",
                # A standalone player page (see player_view.py), meant to be
                # opened in its own tab rather than played inline: a full HA
                # dashboard is a heavy, actively-networking page, and iOS is
                # far more willing to keep a bare, single-purpose audio page
                # alive in the background than a whole SPA.
                "player_url": f"/api/teddycloud/player/{entry_id}/{box_id}/{ruid}",
            }
        )
    library.sort(key=lambda item: (item["title"] or "").lower())
    return library


class TeddyCloudCoordinator(DataUpdateCoordinator[dict[str, TeddyCloudBoxData]]):
    """Polls a teddyCloud server for box status and settings."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: TeddyCloudApiClient,
        entry_id: str,
        sidecar_client: SidecarApiClient | None = None,
        wishlist: Wishlist | None = None,
        catalog: ToniesJsonCatalog | None = None,
        github_source: GitHubNfcSource | None = None,
        backup_import_check_interval: int = DEFAULT_GITHUB_CHECK_INTERVAL * 60,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=UPDATE_INTERVAL),
        )
        self.client = client
        self.entry_id = entry_id
        # Only set when this entry has a teddycloud-nfc-bridge sidecar URL
        # configured — lets the assign_nfc_tag service work per-entry.
        self.sidecar_client = sidecar_client
        self.wishlist = wishlist
        # Feeds the wishlist's search box - see tonies_catalog.py.
        self.catalog = catalog
        # Only set when this entry has a GitHub NFC-backup repo configured —
        # lets the wishlist auto-import (wishlist_backup_import.py) and its
        # on-demand view work per-entry.
        self.github_source = github_source
        # Seconds between GitHub backup-repo checks (config-flow
        # CONF_GITHUB_CHECK_INTERVAL, in minutes, converted by __init__.py) -
        # see _maybe_import_matching_backups.
        self._backup_import_check_interval = backup_import_check_interval
        # Boxes are discovered once on first refresh. A box added to the
        # teddyCloud server later requires reloading the config entry (or
        # restarting HA) to pick up — acceptable for how rarely that happens.
        self.boxes: list[dict] = []
        self._last_backup_import_check: float = 0.0

    async def _async_update_data(self) -> dict[str, TeddyCloudBoxData]:
        if not self.boxes:
            try:
                self.boxes = await self.client.get_boxes()
            except TeddyCloudApiError as err:
                raise UpdateFailed(str(err)) from err

        # Entities are only ever created once, from whatever this method
        # returns on the *first* successful refresh — a box missing here
        # would never get entities until a manual reload. So on the first
        # refresh, any box failing fails the whole thing (HA retries entry
        # setup for everyone, same as before per-box isolation existed).
        # Once entities exist, isolate failures per box instead, so one
        # flaky box doesn't take every other box's entities down with it.
        is_first_refresh = self.data is None

        results = await asyncio.gather(
            *(self._update_box(box) for box in self.boxes), return_exceptions=True
        )

        previous = self.data or {}
        data: dict[str, TeddyCloudBoxData] = {}
        for box, result in zip(self.boxes, results):
            box_id = box["ID"]
            if isinstance(result, TeddyCloudApiError):
                if is_first_refresh:
                    raise UpdateFailed(f"Failed to reach box {box_id}: {result}")
                _LOGGER.warning("teddycloud: failed to update box %s: %s", box_id, result)
                if box_id in previous:
                    data[box_id] = replace(previous[box_id], available=False)
                continue
            if isinstance(result, BaseException):
                raise result
            data[box_id] = result

        if self.wishlist is not None:
            owned_models = {
                tonie["model"]
                for box_data in data.values()
                for tonie in box_data.library
                if tonie.get("model")
            }
            if owned_models:
                await self.wishlist.async_mark_acquired(owned_models)
            await self._maybe_import_matching_backups()

        return data

    async def _maybe_import_matching_backups(self) -> None:
        """Throttled wrapper around async_import_matching_wishlist_items() -
        runs at most once per self._backup_import_check_interval (the
        config flow's CONF_GITHUB_CHECK_INTERVAL), and must never let a
        failure here fail the box poll it's piggybacking on.

        Logs its own early-return reasons (unlike
        async_import_matching_wishlist_items's own logging, which never
        gets a chance to run at all if this wrapper bails out first) -
        without this, someone with debug logging on and nothing
        configured/due yet would see this feature produce zero log
        output whatsoever, indistinguishable from it silently being
        broken.
        """
        if self.github_source is None or self.sidecar_client is None:
            _LOGGER.debug(
                "teddycloud: GitHub backup check skipped - github_source=%s sidecar_client=%s",
                self.github_source is not None,
                self.sidecar_client is not None,
            )
            return
        now = time.monotonic()
        remaining = self._backup_import_check_interval - (now - self._last_backup_import_check)
        if remaining > 0:
            _LOGGER.debug(
                "teddycloud: GitHub backup check skipped - %.0fs until the next one is due", remaining
            )
            return
        self._last_backup_import_check = now
        _LOGGER.debug("teddycloud: running scheduled GitHub backup check")
        try:
            await async_import_matching_wishlist_items(self)
        except Exception:  # noqa: BLE001 - must never break the regular box poll
            _LOGGER.exception("teddycloud: automatic NFC backup import check failed")

    async def _update_box(self, box: dict) -> TeddyCloudBoxData:
        box_id = box["ID"]
        settings, online_text, last_connection_text, last_ruid, ip, tag_index = await asyncio.gather(
            self.client.get_settings_index(box_id),
            self.client.get_setting(SETTING_ONLINE, box_id),
            self.client.get_setting(SETTING_LAST_CONNECTION, box_id),
            self.client.get_setting(SETTING_LAST_RUID, box_id),
            self.client.get_setting(SETTING_IP, box_id),
            self.client.get_tag_index(box_id),
        )

        tonie_info = await self.client.get_tag_info(last_ruid, box_id)

        return TeddyCloudBoxData(
            box=box,
            settings=settings,
            online=_parse_bool(online_text),
            last_connection=_parse_int(last_connection_text),
            ip=ip,
            last_ruid=last_ruid,
            tonie_info=tonie_info,
            library=_build_library(tag_index, self.entry_id, box_id),
        )
