"""Shared fuzzy-text-matching helper.

Both the wishlist's catalog search (tonies_catalog.py) and its GitHub
backup title matching (wishlist_backup_import.py) need the same
accent/case/punctuation-insensitive comparison - a catalog title like
"Pokémon" needs to be found by a search or a filename spelled "Pokemon".
One shared implementation instead of two independently drifting copies.
"""
from __future__ import annotations

import re
import unicodedata

# German umlauts have *two* common ASCII spellings in the wild, not one:
# dropping the diaeresis (ä -> a, which NFKD decomposition + stripping
# combining marks already gives you below) and spelling it out (ä -> ae) -
# a real backup repo folder was named "Siebenschlaefer" for "Siebenschläfer",
# which the diaeresis-drop alone normalizes to "siebenschlafer" - a
# different word from "siebenschlaefer", so a word-set match on it failed
# outright even though every *other* word matched. Applied before the
# generic NFKD pass below, which only ever handles the drop-the-diaeresis
# case for accents in general (é -> e, etc.).
_GERMAN_TRANSLITERATIONS = {
    "ä": "ae",
    "ö": "oe",
    "ü": "ue",
    "ß": "ss",
}


def normalize_for_match(text: str) -> str:
    """Fold `text` to a case/accent/punctuation-insensitive form for
    matching: "&" spelled out as "und", German umlauts spelled out
    (ä -> ae), other diacritics stripped (é -> e) via NFKD decomposition,
    everything that isn't a letter or digit (spaces, -/_/:/. etc.)
    collapsed to single spaces, lowercased."""
    text = text.lower()
    # "Bibi & Tina" vs. a catalog title spelling out "Bibi und Tina" - "&"
    # would otherwise just fold away as punctuation below, dropping the
    # word "und" entirely from whichever side used the symbol instead of
    # spelling it out, the same class of miss as the umlaut spellings.
    text = text.replace("&", " und ")
    for umlaut, replacement in _GERMAN_TRANSLITERATIONS.items():
        text = text.replace(umlaut, replacement)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    # Not just -/_// (the original set): a catalog title can carry other
    # punctuation a filename never would (e.g. a title of "Spider-Man -
    # MARVEL: Spider-Man" left "marvel:" glued together, which then never
    # word-for-word matched a path's plain "marvel"). Any run of anything
    # that isn't a-z/0-9 becomes one space, so this also subsumes the
    # separator and whitespace-collapsing steps in one pass.
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return text.strip()
