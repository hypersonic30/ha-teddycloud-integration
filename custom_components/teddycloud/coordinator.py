"""Data update coordinator for the TeddyCloud integration."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from datetime import timedelta
import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import TeddyCloudApiClient, TeddyCloudApiError
from .const import (
    DOMAIN,
    SETTING_IP,
    SETTING_LAST_CONNECTION,
    SETTING_LAST_RUID,
    SETTING_ONLINE,
    UPDATE_INTERVAL,
)
from .sidecar_api import SidecarApiClient

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
        library.append(
            {
                "ruid": ruid,
                "title": title,
                "series": info.get("series") or None,
                "picture": info.get("picture"),
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
        # Boxes are discovered once on first refresh. A box added to the
        # teddyCloud server later requires reloading the config entry (or
        # restarting HA) to pick up — acceptable for how rarely that happens.
        self.boxes: list[dict] = []

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

        return data

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
