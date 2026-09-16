"""Persistent wishlist of Tonies not yet owned, one per config entry.

Uses Home Assistant's own Store helper (a plain JSON file under
.storage/) rather than a sensor's extra_state_attributes - the same
reasoning as marking the Tonie Library sensor's own attribute
unrecorded: this data doesn't belong in entity state or the recorder at
all, and a wishlist can grow large enough over time to matter.

Cross-referenced against the real library on every coordinator refresh
(see coordinator.py) so an item disappears from the "still wanted" view
on its own once teddyCloud actually has it - matched by tonies.json's
own "model" ID, the same identifier teddyCloud uses internally to look
up a tag's metadata, rather than fuzzy title matching.
"""
from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN

_STORAGE_VERSION = 1


class Wishlist:
    """One wishlist per config entry (per teddyCloud server)."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store: Store = Store(hass, _STORAGE_VERSION, f"{DOMAIN}_wishlist_{entry_id}")
        self._items: list[dict] = []

    async def async_load(self) -> None:
        self._items = await self._store.async_load() or []

    @property
    def items(self) -> list[dict]:
        return self._items

    async def async_add(self, model: str, title: str, series: str | None, picture: str | None) -> None:
        if any(item["model"] == model for item in self._items):
            return
        self._items.append(
            {
                "model": model,
                "title": title,
                "series": series,
                "picture": picture,
                "acquired": False,
            }
        )
        await self._store.async_save(self._items)

    async def async_remove(self, model: str) -> None:
        remaining = [item for item in self._items if item["model"] != model]
        if len(remaining) == len(self._items):
            return
        self._items = remaining
        await self._store.async_save(self._items)

    async def async_mark_acquired(self, owned_models: set[str]) -> None:
        """Flip "acquired" for any wishlist item whose model is now in
        the real library - called after every coordinator refresh."""
        changed = False
        for item in self._items:
            if not item["acquired"] and item["model"] in owned_models:
                item["acquired"] = True
                changed = True
        if changed:
            await self._store.async_save(self._items)
