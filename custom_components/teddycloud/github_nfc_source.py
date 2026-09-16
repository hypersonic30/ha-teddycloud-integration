"""Thin async client for an optional GitHub-hosted backup set of .nfc tag
dumps.

Uses GitHub's Contents API (https://docs.github.com/en/rest/repos/contents)
for both listing a folder and fetching one file's content — deliberately
not the `download_url` a directory listing also returns, since that
field's auth behavior differs between public and private repos. Always
hitting the same authenticated endpoint keeps public/private handling
identical.

A raw .nfc dump carries no title/model metadata of its own - only a
physical tag identity (rUID + cloud-auth bytes) discoverable *after* it's
been uploaded. So matching a file here to a wishlist item is necessarily
by filename (see wishlist_backup_import.py), and the actual restore -
which needs those auth bytes - still goes through the existing
teddycloud-nfc-bridge sidecar, exactly like the assign_nfc_tag service.
This module never talks to that sidecar itself.
"""
from __future__ import annotations

import base64
import logging

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import REQUEST_TIMEOUT

_LOGGER = logging.getLogger(__name__)

_API_VERSION = "2022-11-28"


class GitHubNfcSourceError(Exception):
    """Raised when the configured GitHub backup repo can't be read."""


class GitHubNfcSource:
    """Lists and fetches .nfc files from one folder of one GitHub repo."""

    def __init__(
        self, hass: HomeAssistant, repo: str, branch: str, path: str, token: str | None
    ) -> None:
        self._hass = hass
        self._repo = repo.strip().strip("/")
        self._branch = branch
        self._path = path.strip("/")
        self._token = token or None

    @property
    def _session(self) -> aiohttp.ClientSession:
        return async_get_clientsession(self._hass)

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": _API_VERSION,
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def _contents_url(self, sub_path: str) -> str:
        full_path = f"{self._path}/{sub_path}" if self._path else sub_path
        return f"https://api.github.com/repos/{self._repo}/contents/{full_path}"

    async def _get_json(self, url: str):
        try:
            async with self._session.get(
                url,
                params={"ref": self._branch},
                headers=self._headers(),
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
            ) as resp:
                if resp.status == 404:
                    raise GitHubNfcSourceError(
                        f"{self._repo}: repo/branch/path not found, or the token "
                        "lacks access (private repos need a token with read access)"
                    )
                if resp.status == 403 and resp.headers.get("X-RateLimit-Remaining") == "0":
                    raise GitHubNfcSourceError(
                        "GitHub API rate limit exceeded — configure a token to raise it"
                    )
                resp.raise_for_status()
                return await resp.json()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise GitHubNfcSourceError(str(err)) from err

    async def list_nfc_files(self) -> list[str]:
        """Return the names of every *.nfc file directly inside the
        configured repo/branch/path (not recursive)."""
        entries = await self._get_json(self._contents_url(""))
        if not isinstance(entries, list):
            raise GitHubNfcSourceError(f"{self._path or '/'} is not a folder in {self._repo}")
        return sorted(
            entry["name"]
            for entry in entries
            if entry.get("type") == "file" and entry.get("name", "").lower().endswith(".nfc")
        )

    async def fetch_nfc_file(self, name: str) -> bytes:
        """Return one .nfc file's raw content."""
        entry = await self._get_json(self._contents_url(name))
        if not isinstance(entry, dict) or entry.get("encoding") != "base64":
            raise GitHubNfcSourceError(f"Unexpected response fetching {name} from {self._repo}")
        return base64.b64decode(entry["content"])
