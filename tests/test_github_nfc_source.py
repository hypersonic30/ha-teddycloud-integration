"""Behavioral test for github_nfc_source.py's recursive directory
listing - the real bug this exists to catch: a backup repo organized
into subfolders (e.g. one per person/series) used to only ever see
files in the single configured folder, silently ignoring everything
nested deeper.

Fakes only the network boundary (_get_json, the HTTP call itself) with
a canned directory tree - the recursion logic under test runs for real.
"""
from __future__ import annotations

from custom_components.teddycloud.github_nfc_source import GitHubNfcSource, GitHubNfcSourceError

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


def test_browse_url_for_a_plain_repo_and_branch():
    source = GitHubNfcSource(hass=object(), repo="me/backups", branch="master", path="", token=None)
    assert source.browse_url() == "https://github.com/me/backups/tree/master"


def test_browse_url_includes_the_configured_subfolder_url_encoded():
    source = GitHubNfcSource(hass=object(), repo="me/backups", branch="master", path="German/Familie Sonntag", token=None)
    assert source.browse_url() == "https://github.com/me/backups/tree/master/German/Familie%20Sonntag"


async def test_get_json_error_message_is_never_blank(monkeypatch):
    # Reported: a real log line read "could not list GitHub NFC backups: "
    # with nothing after the colon - str(TimeoutError()) is "" when it's
    # raised with no args, which aiohttp's own timeout handling does. The
    # error message must always say *something*, even then.
    class _RaisesOnEnter:
        def __init__(self, exc):
            self._exc = exc

        async def __aenter__(self):
            raise self._exc

        async def __aexit__(self, *args):
            return False

    class FakeSession:
        def get(self, *args, **kwargs):
            return _RaisesOnEnter(TimeoutError())

    source = GitHubNfcSource(hass=object(), repo="me/backups", branch="master", path="", token=None)
    monkeypatch.setattr(
        "custom_components.teddycloud.github_nfc_source.async_get_clientsession",
        lambda hass: FakeSession(),
    )

    try:
        await source._get_json(source._contents_url(""))
        assert False, "expected GitHubNfcSourceError"
    except GitHubNfcSourceError as err:
        assert str(err).strip() != "", "error message must not be blank"
        assert "TimeoutError" in str(err)
