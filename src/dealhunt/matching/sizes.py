"""Sizing schemes: how to read a size out of listing text.

A scheme turns free text into the sizes it states, on up to two scales. The
matcher then compares those with what the buyer takes (a SizeSpec). Only
"shoe" exists so far; a clothing scheme (S/M/L, 48/50) or a frame scheme
(bike sizes) would be a new entry in SCHEMES, not a change to the matcher.
"""

from __future__ import annotations

import re
from typing import Callable, Dict, Set, Tuple

# ---------------------------------------------------------------------------
# shoe: EU and mondopoint on the main scale, US on the alternate one.
# Sellers mix all of them, and spell mondo three ways ("27.5", "27,5", "275").
# ---------------------------------------------------------------------------

# "Gr. 43", "Grösse: 27,5", "size 10", "mondo 28"
_LABELLED_SIZE_RE = re.compile(
    r"\b(gr|gr\.|gr(?:oe|ö)sse|gr(?:oe|ö)ss|größe|size|taille|mondo(?:point)?|eu|eur|us|uk)"
    r"\s*[.:]?\s*(2[0-9]\d|3[0-3]\d|\d{2}(?:[.,]\d)?|\d(?:[.,]5)?)\b",
    re.I,
)

# A bare "27.5" is always a mondopoint size in this context.
_BARE_MONDO_RE = re.compile(r"\b(2[3-9][.,]\d)\b")

# Ski boots are routinely listed as a bare integer mondo — "Scarpa Maestrale 28".
# The range stops at 32, below the 35-50 EU band and above nothing that collides:
# flex indices start at 90, ski lengths at 150. Guarded on both sides so a
# price, a length or a year cannot be read as a size.
_BARE_MONDO_INT_RE = re.compile(
    r"(?<![\d.,€$/-])\b(2[2-9]|3[0-2])\b(?!\s*(?:cm|mm|%|€|chf|eur|fr|\.-|,-|[\d.,]))",
    re.I,
)

# A bare "43" usually is a size too, but only when it is not a price, a board
# length or a year — hence the guards on both sides.
_BARE_EU_RE = re.compile(
    r"(?<![\d.,€$])\b(3[5-9]|4[0-9]|50)\b(?!\s*(?:cm|mm|%|€|chf|eur|fr|\.-|,-|\d))",
    re.I,
)

# Sellers also write mondo in millimetres with no separator: 275 = 27.5.
_BARE_MM_MONDO_RE = re.compile(
    r"(?:(\w+)[.,:]?\s+)?\b(2[2-9]\d|3[0-1]\d)\b(?!\s*(?:cm|mm|%|€|chf|eur|fr|\.-|,-|[\d.,]))",
    re.I,
)

# Words that mean the number after them is money, not a size.
_PRICE_CONTEXT = frozenset(
    {"np", "neupreis", "preis", "chf", "fr", "eur", "euro", "für", "fuer", "je", "statt", "ca"}
)


def _extract_sizes(text: str) -> Tuple[Set[str], Set[str]]:
    """Split found sizes into EU/mondo and US, since "10" means both."""
    eu: Set[str] = set()
    us: Set[str] = set()

    for label, value in _LABELLED_SIZE_RE.findall(text):
        system = label.lower().rstrip(".")

        # UK sizing is a third scale; treating a UK 9 as EU would invent a
        # size conflict that isn't there, so drop it rather than guess.
        if system == "uk":
            continue

        bucket = us if system == "us" else eu
        bucket.add(normalise_size(value))

    eu.update(normalise_size(v) for v in _BARE_MONDO_RE.findall(text))
    eu.update(normalise_size(v) for v in _BARE_MONDO_INT_RE.findall(text))

    for preceding, value in _BARE_MM_MONDO_RE.findall(text):
        if preceding.lower().strip(".") in _PRICE_CONTEXT:
            continue

        eu.add(normalise_size(value))
    eu.update(normalise_size(v) for v in _BARE_EU_RE.findall(text))

    return eu, us


def normalise_size(value: str) -> str:
    """Fold every spelling onto one: "27,5", "275" and "27.5" are the same boot.

    Sellers write mondopoint three ways — "27.5", "27,5" and the millimetre
    form "275" — and the last one is common on ski boots.
    """
    normalised = value.replace(",", ".")

    if normalised.isdigit() and len(normalised) == 3:
        normalised = "{:.1f}".format(int(normalised) / 10)

    if normalised.endswith(".0"):
        normalised = normalised[:-2]

    return normalised


Extractor = Callable[[str], Tuple[Set[str], Set[str]]]

# Scheme name -> function returning (sizes on the main scale, on the alternate).
SCHEMES: Dict[str, Extractor] = {
    "shoe": _extract_sizes,
}

# Scheme name -> how to normalise a size the buyer writes in their config.
NORMALISERS: Dict[str, Callable[[str], str]] = {
    "shoe": normalise_size,
}
