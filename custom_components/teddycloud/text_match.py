"""Shared fuzzy-text-matching helper.

Both the wishlist's catalog search (tonies_catalog.py) and its GitHub
backup title matching (wishlist_backup_import.py) need the same
accent/case/separator-insensitive substring comparison - a catalog title
like "Pokémon" needs to be found by a search or a filename spelled
"Pokemon". One shared implementation instead of two independently
drifting copies.
"""
from __future__ import annotations

import re
import unicodedata


def normalize_for_match(text: str) -> str:
    """Fold `text` to a case/accent/separator-insensitive form for
    substring matching: diacritics stripped (é -> e) via NFKD
    decomposition, -/_// treated as spaces, whitespace collapsed,
    lowercased."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[-_/]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip().lower()
