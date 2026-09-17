"""Behavioral tests for wishlist_backup_import.py: the filename<->title
matching heuristic (the actual user-facing contract - see README's
Wishlist section on naming backup files after the Tonie) and the
auto-import flow's fan-out-to-every-box + per-upload cache invalidation."""
from __future__ import annotations

from types import SimpleNamespace

from custom_components.teddycloud import wishlist as wishlist_module
from custom_components.teddycloud.const import DOMAIN
from custom_components.teddycloud.wishlist import Wishlist
from custom_components.teddycloud.wishlist_backup_import import (
    _filename_matches_title,
    async_import_matching_wishlist_items,
)

from .conftest import FakeStore


def test_filename_matches_title_handles_separators_and_case():
    assert _filename_matches_title("Die_Eiskoenigin.nfc", "die eiskoenigin")
    assert _filename_matches_title("die-eiskoenigin-teil2.nfc", "Die Eiskoenigin")
    assert not _filename_matches_title("Feuerwehrmann Sam.nfc", "Die Eiskoenigin")


def test_filename_matches_title_across_folder_and_filename():
    # Real reported case: series in the folder name, character in the
    # filename - "Pokemon/Bisasam.nfc" for a wishlist title of
    # "Pokémon - Bisasam". Also covers accent folding (é -> e).
    assert _filename_matches_title("Pokemon/Bisasam.nfc", "Pokémon - Bisasam")
    assert _filename_matches_title("German/Familie Sonntag/Feuerwehrmann Sam.nfc", "Feuerwehrmann Sam")


def test_filename_matches_title_regardless_of_word_order():
    # Real reported case: tonies.json titles aren't consistently ordered -
    # "Pokémon - Bisasam" is Series - Character, but "Spider-Man - Marvel"
    # is Character - Series. The repo had folder "Marvel" holding
    # "Marvel - Spider-Man.nfc" - a plain substring check fails here
    # because the title's word order ("Spider Man Marvel") never appears
    # contiguously in the path ("Marvel Marvel Spider Man"), even though
    # every word is present.
    assert _filename_matches_title("Marvel/Marvel - Spider-Man.nfc", "Spider-Man - Marvel")
    assert _filename_matches_title("Marvel/Spider-Man.nfc", "Spider-Man - Marvel")


def test_filename_matches_title_folds_diacritics():
    assert _filename_matches_title("Die Eiskonigin.nfc", "Die Eiskönigin")
    assert _filename_matches_title("Die Eiskönigin.nfc", "Die Eiskonigin")


class FakeGitHubSource:
    def __init__(self, names, content_by_name) -> None:
        self._names = names
        self._content_by_name = content_by_name
        self.fetched: list[str] = []

    async def list_nfc_files(self):
        return list(self._names)

    async def fetch_nfc_file(self, name: str) -> bytes:
        self.fetched.append(name)
        return self._content_by_name[name]


class FakeSidecarClient:
    def __init__(self, ruid_by_box: dict) -> None:
        self._ruid_by_box = ruid_by_box
        self.calls: list[tuple] = []

    async def upload_nfc_tag(self, filename, content, overlay):
        self.calls.append((filename, content, overlay))
        return {"triggered": True, "ruid": self._ruid_by_box[overlay]}


class FakeCache:
    def __init__(self) -> None:
        self.invalidated: list[str] = []

    async def invalidate(self, key: str) -> None:
        self.invalidated.append(key)


class FakeCoordinator:
    def __init__(self, wishlist, github_source, sidecar_client, boxes, cache) -> None:
        self.wishlist = wishlist
        self.github_source = github_source
        self.sidecar_client = sidecar_client
        self.boxes = boxes
        self.entry_id = "entry1"
        self.hass = SimpleNamespace(data={DOMAIN: {"_content_cache": cache}})


async def _make_wishlist(monkeypatch) -> Wishlist:
    monkeypatch.setattr(wishlist_module, "Store", FakeStore)
    wl = Wishlist(hass=object(), entry_id="entry1")
    await wl.async_load()
    return wl


async def test_import_matches_and_fans_out_to_every_box(monkeypatch):
    wl = await _make_wishlist(monkeypatch)
    await wl.async_add("model-1", "Die Eiskönigin", "Disney", "pic.png")
    await wl.async_add("model-2", "Feuerwehrmann Sam", None, None)
    wl.items[1]["acquired"] = True  # already owned - must not be touched

    # Nested under a subfolder - list_nfc_files() recurses, so this is the
    # common real-world shape (one folder per person/series).
    nested_path = "German/Familie Sonntag/Die Eiskönigin.nfc"
    github_source = FakeGitHubSource(
        names=[nested_path, "unrelated.nfc"],
        content_by_name={nested_path: b"dump-bytes", "unrelated.nfc": b"other"},
    )
    sidecar_client = FakeSidecarClient(ruid_by_box={"box1": "abcd1234", "box2": "abcd1234"})
    cache = FakeCache()
    coordinator = FakeCoordinator(
        wl, github_source, sidecar_client, boxes=[{"ID": "box1"}, {"ID": "box2"}], cache=cache
    )

    attempted = await async_import_matching_wishlist_items(coordinator)

    assert attempted == 2  # one upload per box
    assert github_source.fetched == [nested_path]  # fetched once (full path), reused for both boxes
    assert sidecar_client.calls == [
        ("Die Eiskönigin.nfc", b"dump-bytes", "box1"),  # basename only, for the sidecar
        ("Die Eiskönigin.nfc", b"dump-bytes", "box2"),
    ]
    assert cache.invalidated == ["entry1_box1_abcd1234", "entry1_box2_abcd1234"]


async def test_import_skips_already_acquired_items(monkeypatch):
    wl = await _make_wishlist(monkeypatch)
    await wl.async_add("model-1", "Die Eiskönigin", None, None)
    wl.items[0]["acquired"] = True

    github_source = FakeGitHubSource(
        names=["Die Eiskönigin.nfc"], content_by_name={"Die Eiskönigin.nfc": b"dump"}
    )
    sidecar_client = FakeSidecarClient(ruid_by_box={})
    coordinator = FakeCoordinator(
        wl, github_source, sidecar_client, boxes=[{"ID": "box1"}], cache=FakeCache()
    )

    attempted = await async_import_matching_wishlist_items(coordinator)

    assert attempted == 0
    assert sidecar_client.calls == []


async def test_import_is_noop_without_github_source():
    wl = SimpleNamespace(items=[])
    coordinator = SimpleNamespace(wishlist=wl, github_source=None, sidecar_client=object())
    assert await async_import_matching_wishlist_items(coordinator) == 0
