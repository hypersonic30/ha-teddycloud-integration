"""Thin async client for an optional teddycloud-nfc-bridge sidecar.

The sidecar (https://github.com/hypersonic30/teddycloud-nfc-bridge) is a
separate, standalone service — this integration is just one more caller of
its HTTP API, the same way its own web GUI is. It owns the .nfc parsing and
teddyCloud volume access that Home Assistant itself has no way to do.
"""
from __future__ import annotations

import logging

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import REQUEST_TIMEOUT

_LOGGER = logging.getLogger(__name__)


class SidecarApiError(Exception):
    """Raised when the nfc-bridge sidecar can't be reached or returns an error."""


class SidecarApiClient:
    """Thin async wrapper around the teddycloud-nfc-bridge API."""

    def __init__(self, hass: HomeAssistant, base_url: str) -> None:
        self._hass = hass
        base_url = base_url.rstrip("/")
        if "://" not in base_url:
            base_url = f"http://{base_url}"
        self._base_url = base_url

    @property
    def _session(self) -> aiohttp.ClientSession:
        return async_get_clientsession(self._hass)

    async def upload_nfc_tag(self, filename: str, content: bytes, overlay: str) -> dict:
        """POST a .nfc dump to the sidecar. Returns its JSON response on success."""
        form = aiohttp.FormData()
        form.add_field("overlay", overlay)
        form.add_field("file", content, filename=filename, content_type="application/octet-stream")

        try:
            async with self._session.post(
                f"{self._base_url}/api/nfc-tags",
                data=form,
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT * 4),
            ) as resp:
                body = await resp.json()
                if resp.status >= 400:
                    raise SidecarApiError(body.get("detail", f"HTTP {resp.status}"))
                return body
        except (aiohttp.ClientError, TimeoutError) as err:
            raise SidecarApiError(str(err)) from err
