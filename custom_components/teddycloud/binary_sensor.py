"""Binary sensors for the TeddyCloud integration."""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import TeddyCloudCoordinator
from .entity import TeddyCloudBoxEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: TeddyCloudCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        TeddyCloudOnlineBinarySensor(coordinator, box_id) for box_id in coordinator.data
    )


class TeddyCloudOnlineBinarySensor(TeddyCloudBoxEntity, BinarySensorEntity):
    """Whether the box is currently connected to the teddyCloud server."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_name = "Online"

    def __init__(self, coordinator: TeddyCloudCoordinator, box_id: str) -> None:
        super().__init__(coordinator, box_id)
        self._attr_unique_id = f"{box_id}_online"

    @property
    def is_on(self) -> bool:
        return self._box_data.online

    @property
    def extra_state_attributes(self) -> dict:
        # Lets the card fetch a real product photo from Tonies' own CDN
        # (https://cdn.tonies.de/thumbnails/<box_model>-i.png), the same
        # approach teddyCloud's own web UI uses.
        return {"box_model": self._box_data.box.get("boxModel")}
