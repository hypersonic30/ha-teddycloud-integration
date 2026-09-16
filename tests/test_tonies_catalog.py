"""Behavioral test for tonies_catalog.py's search: accent/diacritic
folding, so a query typed without umlauts/accents still finds a catalog
entry whose title has them (reported: searching "Pokemon" found nothing
for a catalog title of "Pokémon"), and vice versa."""
from __future__ import annotations

from custom_components.teddycloud.tonies_catalog import ToniesJsonCatalog


class FakeClient:
    def __init__(self, entries: list[dict]) -> None:
        self._entries = entries

    async def get_tonies_json_catalog(self):
        return self._entries


def _pokemon_entry() -> dict:
    return {
        "model": "m1",
        "title": "Pokémon - Bisasam",
        "series": "Pokémon",
        "episodes": "Bisasam",
    }


async def test_search_is_accent_insensitive():
    catalog = ToniesJsonCatalog(FakeClient([_pokemon_entry()]))

    results = await catalog.search("pokemon")

    assert len(results) == 1
    assert results[0]["model"] == "m1"


async def test_search_still_matches_the_accented_spelling_too():
    catalog = ToniesJsonCatalog(FakeClient([_pokemon_entry()]))

    results = await catalog.search("pokémon")

    assert len(results) == 1


async def test_search_returns_empty_for_blank_query():
    catalog = ToniesJsonCatalog(FakeClient([_pokemon_entry()]))

    assert await catalog.search("   ") == []
