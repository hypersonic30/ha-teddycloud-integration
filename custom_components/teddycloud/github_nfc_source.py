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

import asyncio
import base64
import logging
from urllib.parse import quote

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

    def _contents_url(self, repo_path: str) -> str:
        url = f"https://api.github.com/repos/{self._repo}/contents"
        return f"{url}/{repo_path}" if repo_path else url

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
            # str(err) is "" for a bare TimeoutError/asyncio.TimeoutError -
            # it's normally raised with no message at all, so a plain
            # str(err) here produced a genuinely blank, useless log line
            # ("could not list GitHub NFC backups: "). Always name the
            # exception type so there's *something* to go on even then.
            detail = str(err) or "no further detail from aiohttp"
            raise GitHubNfcSourceError(f"{type(err).__name__}: {detail}") from err

    async def _list_dir(self, repo_path: str, rel_path: str) -> list[str]:
        """Recursively collect *.nfc file paths under repo_path (a path
        from the repo root, used for the API call), returning each one's
        path relative to the configured self._path folder instead (what
        fetch_nfc_file() and wishlist matching actually want) - a backup
        repo is commonly organized into one folder per person/series
        rather than kept flat, so this doesn't stop at the first level.

        Sibling subfolders are recursed into concurrently (asyncio.gather),
        not one at a time - a repo with many subfolders (one API round
        trip each) would otherwise add up to real, blocking latency during
        config entry setup."""
        entries = await self._get_json(self._contents_url(repo_path))
        if not isinstance(entries, list):
            raise GitHubNfcSourceError(f"{self._path or '/'} is not a folder in {self._repo}")

        names: list[str] = []
        subdirs: list[tuple[str, str]] = []
        for entry in entries:
            name = entry.get("name", "")
            child_repo_path = f"{repo_path}/{name}" if repo_path else name
            child_rel_path = f"{rel_path}/{name}" if rel_path else name
            if entry.get("type") == "dir":
                subdirs.append((child_repo_path, child_rel_path))
            elif entry.get("type") == "file" and name.lower().endswith(".nfc"):
                names.append(child_rel_path)

        if subdirs:
            for sub_names in await asyncio.gather(
                *(self._list_dir(child_repo_path, child_rel_path) for child_repo_path, child_rel_path in subdirs)
            ):
                names.extend(sub_names)

        return names

    async def list_nfc_files(self) -> list[str]:
        """Return the paths (relative to the configured repo/branch/path,
        e.g. "Familie Sonntag/Feuerwehrmann Sam.nfc" for a file in a
        subfolder) of every *.nfc file found anywhere under it."""
        return sorted(await self._list_dir(self._path, ""))

    def browse_url(self) -> str:
        """A human-browsable github.com URL for the configured repo/
        branch/path - for a manual "what's actually in there" check (e.g.
        a link in the card), independent of the API calls above."""
        url = f"https://github.com/{quote(self._repo, safe='/')}/tree/{quote(self._branch, safe='')}"
        if self._path:
            url += "/" + quote(self._path, safe="/")
        return url

    async def fetch_nfc_file(self, relative_path: str) -> bytes:
        """Return one .nfc file's raw content. `relative_path` is relative
        to the configured repo/branch/path, as returned by
        list_nfc_files()."""
        full_path = f"{self._path}/{relative_path}" if self._path else relative_path
        entry = await self._get_json(self._contents_url(full_path))
        if not isinstance(entry, dict) or entry.get("encoding") != "base64":
            raise GitHubNfcSourceError(
                f"Unexpected response fetching {relative_path} from {self._repo}"
            )
        return base64.b64decode(entry["content"])
