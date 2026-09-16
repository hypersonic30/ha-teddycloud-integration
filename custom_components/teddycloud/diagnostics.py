"""Diagnostics support for the TeddyCloud integration.

Deliberately leaves Tonie titles/pictures out of the box summaries below
(just a library count) even though they're not top-secret — this runs on
a kids' product, and diagnostics get attached to bug reports other people
will read, so there's no reason to include a child's library of story
titles when a count says just as much for debugging.
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_GITHUB_TOKEN, CONF_HOST, CONF_SIDECAR_URL, DOMAIN

TO_REDACT = {CONF_HOST, CONF_SIDECAR_URL, CONF_GITHUB_TOKEN, "ip"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]

    boxes: dict[str, Any] = {}
    for box_id, box_data in (coordinator.data or {}).items():
        boxes[box_id] = {
            "online": box_data.online,
            "available": box_data.available,
            "last_connection": box_data.last_connection,
            "ip": box_data.ip,
            "settings": box_data.settings,
            "library_count": len(box_data.library),
            "has_tonie_info": box_data.tonie_info is not None,
        }

    return async_redact_data(
        {
            "entry_data": dict(entry.data),
            "sidecar_configured": coordinator.sidecar_client is not None,
            "github_backup_source_configured": coordinator.github_source is not None,
            "box_count": len(coordinator.boxes),
            "boxes": boxes,
        },
        TO_REDACT,
    )
