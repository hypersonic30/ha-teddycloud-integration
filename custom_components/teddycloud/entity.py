"""Base entity for TeddyCloud box entities."""
from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import TeddyCloudBoxData, TeddyCloudCoordinator


class TeddyCloudBoxEntity(CoordinatorEntity[TeddyCloudCoordinator]):
    """Common device_info plumbing for entities tied to one box."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: TeddyCloudCoordinator, box_id: str) -> None:
        super().__init__(coordinator)
        self._box_id = box_id
        box = coordinator.data[box_id].box
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, box_id)},
            name=box.get("boxName") or box.get("commonName") or box_id,
            manufacturer="tonies",
            model=box.get("boxModel"),
        )

    @property
    def _box_data(self) -> TeddyCloudBoxData:
        return self.coordinator.data[self._box_id]

    @property
    def available(self) -> bool:
        # The base CoordinatorEntity only tracks one success flag for the
        # whole coordinator, which stays True as long as any box is
        # reachable — this box specifically may be carrying forward stale
        # data because its own fetch failed this round.
        return super().available and self._box_data.available
