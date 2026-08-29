"""Minimal REST client for a teddyCloud server.

teddyCloud's settings API is intentionally simple: reads/writes of a single
key are plain text (no JSON envelope), and /api/settings/set/<key> replies
with HTTP 200 and an empty body even for a key that doesn't exist — so the
only reliable way to confirm a write took effect is to read the value back
afterwards. The coordinator does that by requesting a refresh right after any
write instead of trusting the response body.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import REQUEST_TIMEOUT

_LOGGER = logging.getLogger(__name__)


class TeddyCloudApiError(Exception):
    """Raised when a teddyCloud server can't be reached or returns an error."""


class TeddyCloudApiClient:
    """Thin async wrapper around the teddyCloud REST API."""

    def __init__(self, hass: HomeAssistant, base_url: str, verify_ssl: bool = True) -> None:
        self._hass = hass
        self._base_url = base_url.rstrip("/")
        self._verify_ssl = verify_ssl

    @property
    def _session(self) -> aiohttp.ClientSession:
        return async_get_clientsession(self._hass, verify_ssl=self._verify_ssl)

    async def _get_text(self, path: str, params: dict | None = None) -> str:
        try:
            async with self._session.get(
                f"{self._base_url}{path}",
                params=params,
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
            ) as resp:
                resp.raise_for_status()
                return await resp.text()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise TeddyCloudApiError(str(err)) from err

    async def _get_json(self, path: str, params: dict | None = None) -> Any:
        text = await self._get_text(path, params)
        try:
            return json.loads(text)
        except json.JSONDecodeError as err:
            raise TeddyCloudApiError(f"Invalid JSON from {path}: {err}") from err

    async def get_boxes(self) -> list[dict]:
        """Return the list of boxes known to this teddyCloud server."""
        data = await self._get_json("/api/getBoxes")
        return data.get("boxes", [])

    async def get_settings_index(self, overlay: str) -> dict[str, Any]:
        """Return {settingID: typed value} for every non-internal setting."""
        data = await self._get_json("/api/settings/getIndex", params={"overlay": overlay})
        return {opt["ID"]: opt["value"] for opt in data.get("options", [])}

    async def get_setting(self, key: str, overlay: str) -> str:
        """Return the raw text value of a single setting (incl. internal.* keys)."""
        text = await self._get_text(f"/api/settings/get/{key}", params={"overlay": overlay})
        return text.strip()

    async def set_setting(self, key: str, overlay: str, value: str) -> None:
        """Write a single setting. Success can only be confirmed by re-reading it."""
        try:
            async with self._session.post(
                f"{self._base_url}/api/settings/set/{key}",
                params={"overlay": overlay},
                data=value,
                headers={"Content-Type": "text/plain"},
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
            ) as resp:
                resp.raise_for_status()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise TeddyCloudApiError(str(err)) from err

    async def get_tag_info(self, ruid: str, overlay: str) -> dict | None:
        """Return the tagInfo dict for a ruid, or None if unavailable."""
        if not ruid:
            return None
        try:
            data = await self._get_json("/api/getTagInfo", params={"ruid": ruid, "overlay": overlay})
        except TeddyCloudApiError as err:
            _LOGGER.debug("teddycloud: getTagInfo failed for ruid %s: %s", ruid, err)
            return None
        return data.get("tagInfo")
