"""Select entities for the TeddyCloud integration."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, LED_OPTIONS, SETTING_LED, SETTING_MAX_VOL_HDP, SETTING_MAX_VOL_SPK, VOLUME_OPTIONS
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
                TeddyCloudMaxVolumeSpeakerSelect(coordinator, box_id),
                TeddyCloudMaxVolumeHeadphonesSelect(coordinator, box_id),
                TeddyCloudLedModeSelect(coordinator, box_id),
            ]
        )
    async_add_entities(entities)


class TeddyCloudMappedSettingSelect(TeddyCloudBoxEntity, SelectEntity):
    """A select entity backed by an integer teddyCloud setting with fixed options."""

    _setting_key: str
    _value_to_option: dict[str, str]

    def __init__(self, coordinator: TeddyCloudCoordinator, box_id: str) -> None:
        super().__init__(coordinator, box_id)
        self._attr_unique_id = f"{box_id}_{self._setting_key.rsplit('.', 1)[-1]}"
        self._attr_options = list(self._value_to_option.values())
        self._option_to_value = {v: k for k, v in self._value_to_option.items()}

    @property
    def current_option(self) -> str | None:
        raw = self._box_data.settings.get(self._setting_key)
        return self._value_to_option.get(str(raw))

    async def async_select_option(self, option: str) -> None:
        value = self._option_to_value[option]
        await self.coordinator.client.set_setting(self._setting_key, self._box_id, value)
        await self.coordinator.async_request_refresh()


class TeddyCloudMaxVolumeSpeakerSelect(TeddyCloudMappedSettingSelect):
    """Speaker volume limit."""

    _setting_key = SETTING_MAX_VOL_SPK
    _value_to_option = VOLUME_OPTIONS
    _attr_name = "Max Volume Speaker"
    _attr_icon = "mdi:volume-high"


class TeddyCloudMaxVolumeHeadphonesSelect(TeddyCloudMappedSettingSelect):
    """Headphone volume limit."""

    _setting_key = SETTING_MAX_VOL_HDP
    _value_to_option = VOLUME_OPTIONS
    _attr_name = "Max Volume Headphones"
    _attr_icon = "mdi:headphones"


class TeddyCloudLedModeSelect(TeddyCloudMappedSettingSelect):
    """LED brightness mode."""

    _setting_key = SETTING_LED
    _value_to_option = LED_OPTIONS
    _attr_name = "LED Mode"
    _attr_icon = "mdi:led-on"
