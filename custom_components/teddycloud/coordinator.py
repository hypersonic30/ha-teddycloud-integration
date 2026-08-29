"""Data update coordinator for the TeddyCloud integration."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
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


def _parse_bool(text: str) -> bool:
    return text.strip().lower() == "true"


def _parse_int(text: str) -> int | None:
    text = text.strip()
    return int(text) if text.lstrip("-").isdigit() else None


class TeddyCloudCoordinator(DataUpdateCoordinator[dict[str, TeddyCloudBoxData]]):
    """Polls a teddyCloud server for box status and settings."""

    def __init__(self, hass: HomeAssistant, client: TeddyCloudApiClient) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=UPDATE_INTERVAL),
        )
        self.client = client
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

        results = await asyncio.gather(
            *(self._update_box(box) for box in self.boxes), return_exceptions=True
        )

        # A single box's transient API failure shouldn't take every other
        # box's entities down too — fall back to that box's last-known-good
        # data (if any) instead of failing the whole refresh.
        previous = self.data or {}
        data: dict[str, TeddyCloudBoxData] = {}
        for box, result in zip(self.boxes, results):
            box_id = box["ID"]
            if isinstance(result, TeddyCloudApiError):
                _LOGGER.warning("teddycloud: failed to update box %s: %s", box_id, result)
                if box_id in previous:
                    data[box_id] = previous[box_id]
                continue
            if isinstance(result, BaseException):
                raise result
            data[box_id] = result

        if not data:
            raise UpdateFailed("Failed to update any box")
        return data

    async def _update_box(self, box: dict) -> TeddyCloudBoxData:
        box_id = box["ID"]
        settings, online_text, last_connection_text, last_ruid, ip = await asyncio.gather(
            self.client.get_settings_index(box_id),
            self.client.get_setting(SETTING_ONLINE, box_id),
            self.client.get_setting(SETTING_LAST_CONNECTION, box_id),
            self.client.get_setting(SETTING_LAST_RUID, box_id),
            self.client.get_setting(SETTING_IP, box_id),
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
        )
