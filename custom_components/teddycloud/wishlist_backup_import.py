"""Matches un-acquired wishlist items against a configured GitHub NFC
backup repo by filename, and auto-imports any match through the same
teddycloud-nfc-bridge sidecar upload the assign_nfc_tag service uses.

Matching is necessarily by filename/path, not by the tag's rUID: a raw
.nfc dump carries no title/model metadata of its own - only a physical
tag identity discoverable *after* it's uploaded. So this relies on the
user naming backup files (and, per real-world feedback, folders)
recognizably after the Tonie itself (see README's Wishlist section).

Matching runs against the *whole* relative path, folders included, not
just the bare filename: a common real organization splits a Tonie's
identity across the two, e.g. a "Pokemon" folder holding "Bisasam.nfc"
for a wishlist title of "Pokémon - Bisasam" - matching only the filename
would never find that. Accents/diacritics are also folded (é -> e), since
plain filenames often drop them even when the catalog title doesn't.

Matching is by *word set*, not substring: tonies.json's own titles aren't
consistently ordered ("Pokémon - Bisasam" is Series - Character, but
"Spider-Man - Marvel" is Character - Series), and a folder/filename split
only ever reproduces whichever order the user happened to type - a
straight substring check would demand the title's words appear in that
same order somewhere in the path, which fails for exactly the reversed-
order titles most likely to show up. Every word of the title has to
appear *somewhere* in the path instead, in any order.

Imports to *every* box on the entry, not just one: a physical tag's
content isn't box-specific (that's the whole point of a Tonie figure),
and wishlist.async_mark_acquired's own "owned" check is already
entry-wide (any box's library counts) - this just mirrors that.

Title words in `_TITLE_FILLER_WORDS` don't have to appear in the path at
all: tonies.json commonly titles a multi-track figure "<Story> und
weitere Folgen/Geschichten/Abenteuer/Erzählungen" (bonus-content
boilerplate, not part of what identifies the figure), but a backup's own
filename often just names the main story - real case: catalog title
"Bobo Siebenschläfer - Bobo beim Kinderarzt und weitere Folgen" for a
backup file simply named "Bobo beim Kinderarzt.nfc" (siblings in the
same folder *do* spell out "und weitere Folgen" in their own filenames,
confirming this is inconsistent per-file, not a naming convention to
rely on). Requiring every title word in the path (as normal) would
never match this - these specific filler words are exempted from that
requirement instead, while every other title word still has to be
present, same as before.
"""
from __future__ import annotations

import logging

from .const import DOMAIN
from .github_nfc_source import GitHubNfcSourceError
from .sidecar_api import SidecarApiError
from .text_match import normalize_for_match

_LOGGER = logging.getLogger(__name__)

_TITLE_FILLER_WORDS = {
    "und",
    "weitere",
    "folgen",
    "geschichten",
    "abenteuer",
    "erzaehlungen",
}


def _filename_matches_title(path: str, title: str) -> bool:
    """`path` may include subfolders (github_nfc_source.list_nfc_files()
    recurses) - matched against the whole path by word set, see module
    docstring for why not a substring/order-sensitive check, and for why
    `_TITLE_FILLER_WORDS` are dropped from the requirement rather than
    matched normally."""
    stem = path.rsplit(".", 1)[0]
    title_words = [
        word for word in normalize_for_match(title).split() if word not in _TITLE_FILLER_WORDS
    ]
    if not title_words:
        return False
    path_words = set(normalize_for_match(stem).split())
    return all(word in path_words for word in title_words)


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
        _LOGGER.debug(
            "teddycloud: skipping GitHub backup check - wishlist=%s github_source=%s sidecar_client=%s",
            coordinator.wishlist is not None,
            coordinator.github_source is not None,
            coordinator.sidecar_client is not None,
        )
        return 0

    pending_items = [
        item for item in coordinator.wishlist.items if not item["acquired"] and item.get("title")
    ]
    pending_titles = {item["title"] for item in pending_items}
    if not pending_titles:
        _LOGGER.info("teddycloud: GitHub backup check - wishlist has nothing pending, skipping")
        return 0

    _LOGGER.info(
        "teddycloud: GitHub backup check - %d pending wishlist title(s): %s",
        len(pending_titles),
        sorted(pending_titles),
    )

    try:
        names = await coordinator.github_source.list_nfc_files()
    except GitHubNfcSourceError as err:
        _LOGGER.warning("teddycloud: could not list GitHub NFC backups: %s", err)
        return 0

    _LOGGER.info(
        "teddycloud: GitHub backup check - found %d .nfc file(s) in the repo: %s", len(names), names
    )

    matches = [
        (name, title) for name in names for title in pending_titles if _filename_matches_title(name, title)
    ]

    # The most directly useful line for "why wasn't my Tonie found": which
    # pending titles matched *nothing* in the listing, by name - callers
    # debugging a specific miss should be able to read the answer straight
    # off this one line instead of reasoning about the whole batch.
    unmatched = pending_titles - {title for _, title in matches}
    if unmatched:
        _LOGGER.info(
            "teddycloud: GitHub backup check - no matching .nfc file for: %s", sorted(unmatched)
        )

    if not matches:
        return 0

    _LOGGER.info(
        "teddycloud: GitHub backup check - matched: %s",
        [f"{name!r} -> {title!r}" for name, title in matches],
    )

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
        any_box_succeeded = False
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
            any_box_succeeded = True
            ruid = result.get("ruid")
            _LOGGER.info(
                "teddycloud: imported backup %s (%r) to box %s (ruid=%s)", name, title, box_id, ruid
            )
            if ruid and cache is not None:
                await cache.invalidate(f"{coordinator.entry_id}_{box_id}_{ruid}")

        if any_box_succeeded:
            # Mark the matched wishlist item acquired directly instead of
            # only relying on the coordinator's regular model-based cross-
            # reference (wishlist.async_mark_acquired, driven by whatever
            # "model" teddyCloud's own tonieInfo reports for the newly
            # downloaded content). A real report showed those two models
            # disagreeing even though the identical physical tag was now
            # genuinely present (title/picture both correct in the box's
            # library) - likely a duplicate/regional tonies.json catalog
            # entry - leaving the wishlist stuck un-struck-through despite
            # a fully successful, sidecar-confirmed import. We already
            # know exactly which wishlist entry this was (matched by
            # title), so there's no need to wait on that agreeing.
            matching_item = next((item for item in pending_items if item["title"] == title), None)
            if matching_item and matching_item.get("model"):
                await coordinator.wishlist.async_mark_acquired({matching_item["model"]})

    return attempted
