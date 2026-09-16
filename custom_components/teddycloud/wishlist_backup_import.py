"""Matches un-acquired wishlist items against a configured GitHub NFC
backup repo by filename, and auto-imports any match through the same
teddycloud-nfc-bridge sidecar upload the assign_nfc_tag service uses.

Matching is necessarily by filename, not by the tag's rUID: a raw .nfc
dump carries no title/model metadata of its own - only a physical tag
identity discoverable *after* it's uploaded. So this relies on the user
naming backup files recognizably after the Tonie itself (see README's
Wishlist section) and reuses the same case/separator-insensitive
substring match tonies_catalog.py already uses for the wishlist's own
search box.

Imports to *every* box on the entry, not just one: a physical tag's
content isn't box-specific (that's the whole point of a Tonie figure),
and wishlist.async_mark_acquired's own "owned" check is already
entry-wide (any box's library counts) - this just mirrors that.
"""
from __future__ import annotations

import logging
import re

from .const import DOMAIN
from .github_nfc_source import GitHubNfcSourceError
from .sidecar_api import SidecarApiError

_LOGGER = logging.getLogger(__name__)


def _normalize(text: str) -> str:
    return re.sub(r"[-_]+", " ", text).strip().lower()


def _filename_matches_title(path: str, title: str) -> bool:
    """`path` may include subfolders (github_nfc_source.list_nfc_files()
    recurses) - matched against its basename only, so an ancestor folder
    name can't cause a false-positive match."""
    basename = path.rsplit("/", 1)[-1]
    stem = basename.rsplit(".", 1)[0]
    return _normalize(title) in _normalize(stem)


async def async_import_matching_wishlist_items(coordinator) -> int:
    """For every un-acquired wishlist item whose title matches a .nfc
    filename in the configured GitHub repo, fetch that file once and
    upload it via the sidecar to every box on this entry - the same
    restore assign_nfc_tag performs for a single manually-picked file,
    just auto-triggered and fanned out to every box.

    Returns how many (filename, box) uploads were attempted. Per-file and
    per-box failures are logged and skipped rather than aborting the rest
    of the batch. No-op (returns 0) if wishlist/github_source/
    sidecar_client aren't all configured for this entry, or nothing is
    pending. Callers are responsible for requesting a coordinator refresh
    afterward if they want the wishlist's "acquired" flip to reflect
    immediately rather than on the next regular poll.
    """
    if coordinator.wishlist is None or coordinator.github_source is None or coordinator.sidecar_client is None:
        return 0

    pending_titles = {
        item["title"] for item in coordinator.wishlist.items if not item["acquired"] and item.get("title")
    }
    if not pending_titles:
        return 0

    try:
        names = await coordinator.github_source.list_nfc_files()
    except GitHubNfcSourceError as err:
        _LOGGER.warning("teddycloud: could not list GitHub NFC backups: %s", err)
        return 0

    matches = [
        (name, title) for name in names for title in pending_titles if _filename_matches_title(name, title)
    ]
    if not matches:
        return 0

    cache = coordinator.hass.data.get(DOMAIN, {}).get("_content_cache")
    attempted = 0
    for name, title in matches:
        try:
            content = await coordinator.github_source.fetch_nfc_file(name)
        except GitHubNfcSourceError as err:
            _LOGGER.warning("teddycloud: could not fetch backup %s for %r: %s", name, title, err)
            continue

        # Just the basename for the sidecar - any subfolder structure is
        # our own organizational convention, not meaningful to teddyCloud.
        basename = name.rsplit("/", 1)[-1]
        for box in coordinator.boxes:
            box_id = box["ID"]
            try:
                result = await coordinator.sidecar_client.upload_nfc_tag(basename, content, overlay=box_id)
            except SidecarApiError as err:
                _LOGGER.warning(
                    "teddycloud: could not import backup %s to box %s: %s", name, box_id, err
                )
                continue
            attempted += 1
            ruid = result.get("ruid")
            if ruid and cache is not None:
                await cache.invalidate(f"{coordinator.entry_id}_{box_id}_{ruid}")

    return attempted
