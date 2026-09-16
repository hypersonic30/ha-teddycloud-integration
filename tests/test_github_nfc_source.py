"""Behavioral test for github_nfc_source.py's recursive directory
listing - the real bug this exists to catch: a backup repo organized
into subfolders (e.g. one per person/series) used to only ever see
files in the single configured folder, silently ignoring everything
nested deeper.

Fakes only the network boundary (_get_json, the HTTP call itself) with
a canned directory tree - the recursion logic under test runs for real.
"""
from __future__ import annotations

from custom_components.teddycloud.github_nfc_source import GitHubNfcSource

# German/                     (the configured path)
#   loose.nfc
#   not-an-nfc.txt
#   Familie Sonntag/
#     Feuerwehrmann Sam.nfc
#     Die Eiskönigin.nfc
_TREE = {
    "German": [
        {"name": "Familie Sonntag", "type": "dir"},
        {"name": "loose.nfc", "type": "file"},
        {"name": "not-an-nfc.txt", "type": "file"},
    ],
    "German/Familie Sonntag": [
        {"name": "Feuerwehrmann Sam.nfc", "type": "file"},
        {"name": "Die Eiskönigin.nfc", "type": "file"},
    ],
}


async def test_list_nfc_files_recurses_into_subfolders(monkeypatch):
    source = GitHubNfcSource(hass=object(), repo="me/backups", branch="master", path="German", token=None)

    async def fake_get_json(url):
        repo_path = url.rsplit("/contents/", 1)[-1]
        return _TREE[repo_path]

    monkeypatch.setattr(source, "_get_json", fake_get_json)

    names = await source.list_nfc_files()

    assert sorted(names) == sorted(
        [
            "loose.nfc",
            "Familie Sonntag/Feuerwehrmann Sam.nfc",
            "Familie Sonntag/Die Eiskönigin.nfc",
        ]
    )


async def test_fetch_nfc_file_requests_path_relative_to_configured_folder(monkeypatch):
    source = GitHubNfcSource(hass=object(), repo="me/backups", branch="master", path="German", token=None)
    seen_urls = []

    async def fake_get_json(url):
        seen_urls.append(url)
        return {"encoding": "base64", "content": "aGVsbG8="}  # "hello"

    monkeypatch.setattr(source, "_get_json", fake_get_json)

    content = await source.fetch_nfc_file("Familie Sonntag/Feuerwehrmann Sam.nfc")

    assert content == b"hello"
    assert seen_urls == [
        "https://api.github.com/repos/me/backups/contents/German/Familie Sonntag/Feuerwehrmann Sam.nfc"
    ]
