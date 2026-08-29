"""Sensors for the TeddyCloud integration."""
from __future__ import annotations

from datetime import datetime, timezone

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
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
    entities: list[TeddyCloudBoxEntity] = []
    for box_id in coordinator.data:
        entities.extend(
            [
                TeddyCloudLastConnectionSensor(coordinator, box_id),
                TeddyCloudLastIpSensor(coordinator, box_id),
                TeddyCloudCurrentTonieSensor(coordinator, box_id),
                TeddyCloudCurrentTonieSeriesSensor(coordinator, box_id),
            ]
        )
    async_add_entities(entities)


class TeddyCloudLastConnectionSensor(TeddyCloudBoxEntity, SensorEntity):
    """Timestamp of the box's last connection to the teddyCloud server."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_name = "Last Connection"

    def __init__(self, coordinator: TeddyCloudCoordinator, box_id: str) -> None:
        super().__init__(coordinator, box_id)
        self._attr_unique_id = f"{box_id}_last_connection"

    @property
    def native_value(self) -> datetime | None:
        last_connection = self._box_data.last_connection
        if not last_connection:
            return None
        return datetime.fromtimestamp(last_connection, tz=timezone.utc)


class TeddyCloudLastIpSensor(TeddyCloudBoxEntity, SensorEntity):
    """The box's last known local IP address."""

    _attr_name = "Last IP"
    _attr_icon = "mdi:ip-network"

    def __init__(self, coordinator: TeddyCloudCoordinator, box_id: str) -> None:
        super().__init__(coordinator, box_id)
        self._attr_unique_id = f"{box_id}_last_ip"

    @property
    def native_value(self) -> str | None:
        return self._box_data.ip or None


class TeddyCloudCurrentTonieSensor(TeddyCloudBoxEntity, SensorEntity):
    """Title/cover/metadata of the most recently placed Tonie figure."""

    _attr_name = "Current Tonie"
    _attr_icon = "mdi:teddy-bear"

    def __init__(self, coordinator: TeddyCloudCoordinator, box_id: str) -> None:
        super().__init__(coordinator, box_id)
        self._attr_unique_id = f"{box_id}_current_tonie"

    @property
    def _tonie_info(self) -> dict | None:
        tag_info = self._box_data.tonie_info
        return tag_info.get("tonieInfo") if tag_info else None

    @property
    def native_value(self) -> str | None:
        info = self._tonie_info
        if not info:
            return None
        return info.get("episode") or info.get("series") or None

    @property
    def entity_picture(self) -> str | None:
        info = self._tonie_info
        return info.get("picture") if info else None

    @property
    def extra_state_attributes(self) -> dict:
        tag_info = self._box_data.tonie_info
        info = self._tonie_info
        if not tag_info or not info:
            return {}
        return {
            "ruid": tag_info.get("ruid"),
            "valid": tag_info.get("valid"),
            "exists": tag_info.get("exists"),
            "series": info.get("series"),
            "model": info.get("model"),
            "language": info.get("language"),
            "tracks": info.get("tracks"),
        }


class TeddyCloudCurrentTonieSeriesSensor(TeddyCloudBoxEntity, SensorEntity):
    """Series name of the most recently placed Tonie figure."""

    _attr_name = "Current Tonie Series"
    _attr_icon = "mdi:teddy-bear"

    def __init__(self, coordinator: TeddyCloudCoordinator, box_id: str) -> None:
        super().__init__(coordinator, box_id)
        self._attr_unique_id = f"{box_id}_current_tonie_series"

    @property
    def native_value(self) -> str | None:
        tag_info = self._box_data.tonie_info
        info = tag_info.get("tonieInfo") if tag_info else None
        return info.get("series") if info else None
