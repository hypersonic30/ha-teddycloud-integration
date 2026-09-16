"""Shared test fakes.

Per CLAUDE.md: tests run against the real installed `homeassistant`
package, faking only genuinely-hard-to-construct runtime objects (Store,
device registry, HomeAssistant itself) - never the logic actually under
test.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class FakeStore:
    """Stands in for homeassistant.helpers.storage.Store: an in-memory
    slot instead of a real on-disk JSON file. Exercising HA's actual
    storage machinery would need a fully booted HomeAssistant instance,
    for no benefit to the wishlist logic under test here."""

    def __init__(self, *args, **kwargs) -> None:
        self.saved: list[dict] | None = None

    async def async_load(self):
        return self.saved

    async def async_save(self, data) -> None:
        self.saved = data


class FakeDeviceEntry:
    def __init__(self, identifiers, config_entries) -> None:
        self.identifiers = identifiers
        self.config_entries = config_entries


class FakeDeviceRegistry:
    """Stands in for homeassistant.helpers.device_registry's registry -
    a plain dict lookup instead of the real, storage-backed registry."""

    def __init__(self, devices: dict[str, FakeDeviceEntry]) -> None:
        self._devices = devices

    def async_get(self, device_id: str):
        return self._devices.get(device_id)
