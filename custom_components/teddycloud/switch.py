"""Switches for the TeddyCloud integration."""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    SETTING_CLOUD_CACHE_CONTENT,
    SETTING_CLOUD_ENABLED,
    SETTING_SLAP_BACK_LEFT,
    SETTING_SLAP_ENABLED,
)
from .coordinator import TeddyCloudCoordinator
from .entity import TeddyCloudBoxEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: TeddyCloudCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[TeddyCloudBoxEntity] = []
    for box_id in coordinator.data:
        entities.extend(
            [
                TeddyCloudCloudEnabledSwitch(coordinator, box_id),
                TeddyCloudCacheContentSwitch(coordinator, box_id),
                TeddyCloudSlapEnabledSwitch(coordinator, box_id),
                TeddyCloudSlapDirectionSwitch(coordinator, box_id),
            ]
        )
    async_add_entities(entities)


class TeddyCloudSettingSwitch(TeddyCloudBoxEntity, SwitchEntity):
    """A switch that reads/writes one boolean teddyCloud setting."""

    _setting_key: str

    def __init__(self, coordinator: TeddyCloudCoordinator, box_id: str) -> None:
        super().__init__(coordinator, box_id)
        self._attr_unique_id = f"{box_id}_{self._setting_key.rsplit('.', 1)[-1]}"

    @property
    def is_on(self) -> bool:
        # getIndex returns real JSON booleans (verified live) — compare with
        # `is True` rather than truthiness so a stray non-bool value reads as
        # off instead of silently reading as on.
        return self._box_data.settings.get(self._setting_key) is True

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_set(False)

    async def _async_set(self, value: bool) -> None:
        await self.coordinator.client.set_setting(
            self._setting_key, self._box_id, "true" if value else "false"
        )
        await self.coordinator.async_request_refresh()


class TeddyCloudCloudEnabledSwitch(TeddyCloudSettingSwitch):
    """Enable/disable cloud operation for this box."""

    _setting_key = SETTING_CLOUD_ENABLED
    _attr_name = "Cloud Enabled"
    _attr_icon = "mdi:cloud"


class TeddyCloudCacheContentSwitch(TeddyCloudSettingSwitch):
    """Cache cloud content locally on the teddyCloud server."""

    _setting_key = SETTING_CLOUD_CACHE_CONTENT
    _attr_name = "Cache Content"
    _attr_icon = "mdi:cloud-download"


class TeddyCloudSlapEnabledSwitch(TeddyCloudSettingSwitch):
    """Enable track-skip via slapping gesture."""

    _setting_key = SETTING_SLAP_ENABLED
    _attr_name = "Slap To Skip"
    _attr_icon = "mdi:gesture-tap"


class TeddyCloudSlapDirectionSwitch(TeddyCloudSettingSwitch):
    """Slap direction for skipping: off = left-backward, on = left-forward."""

    _setting_key = SETTING_SLAP_BACK_LEFT
    _attr_name = "Slap Direction Forward On Left"
    _attr_icon = "mdi:gesture-swipe"
