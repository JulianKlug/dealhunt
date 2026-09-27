"""vinted.fr — Europe's biggest second-hand app, strong on goggles and boots.

The site ships an empty app shell and renders listings in JavaScript, so it
needs the BROWSER transport. The rendered item links carry an accessibility
label that is structured enough to parse without touching any CSS:

    "Scarponi snowboard Northwawe, Marque: NORTHWAVE, État: Très bon état,
     Taille: 42,5, 35.00 €, 37.45 €"

The second price includes Vinted's buyer-protection fee, which is what you pay.
Vinted delivers to the French address, not to Switzerland.
"""

from __future__ import annotations

import logging
import re
from typing import List, Optional
from urllib.parse import urlencode, urljoin

from bs4 import BeautifulSoup

from dealhunt.models import RawListing, SearchQuery, SourceResult
from dealhunt.net.client import HttpError, HttpClient, Transport

log = logging.getLogger(__name__)

SOURCE = "vinted"
# Where listings ship from; the buyer\'s config decides what that costs.
COUNTRY = "FR"
TRANSPORT = Transport.BROWSER
BASE_URL = "https://www.vinted.fr"

_ITEM_ID_RE = re.compile(r"/items/(\d+)")
_PRICE_RE = re.compile(r"(\d+(?:[.,]\d{2})?)\s*€")

# The label lists attributes after the title; the first of them ends the title.
_ATTRIBUTE_START_RE = re.compile(r",\s*(?:Marque|État|Etat|Taille)\s*:", re.I)


def search_url(text: str) -> str:
    return f"{BASE_URL}/catalog?" + urlencode({"search_text": text, "order": "newest_first"})


def fetch(client: HttpClient, queries: List[SearchQuery]) -> SourceResult:
    result = SourceResult(source=SOURCE)

    # One rendered page per query: ~150 items, newest first, at ~6 s a load.
    for query in queries:
        try:
            html = client.get_text(search_url(query.text))
        except HttpError as exc:
            log.warning("vinted %r failed: %s", query.text, exc)
            result.error = str(exc)

            if exc.is_block:
                result.blocked = True
                break

            continue

        result.listings.extend(parse(html))

    return result


def parse(html: str) -> List[RawListing]:
    soup = BeautifulSoup(html, "lxml")
    listings = {}

    for anchor in soup.select('a[href*="/items/"][title]'):
        listing = _to_listing(anchor["href"], anchor["title"])

        # Each item appears under several links; the first carries the label.
        if listing is not None and listing.listing_id not in listings:
            listings[listing.listing_id] = listing

    return list(listings.values())


def _to_listing(href: str, label: str) -> Optional[RawListing]:
    match = _ITEM_ID_RE.search(href)
    if not match:
        return None

    split = _ATTRIBUTE_START_RE.search(label)
    title = label[: split.start()] if split else label
    attributes = label[split.start() + 1 :].strip() if split else ""

    return RawListing(
        source=SOURCE,
        listing_id=match.group(1),
        title=title.strip(),
        description=attributes,
        price=_price(label),
        currency="EUR",
        # The rendered DOM uses relative links: "/items/100…-smith-squad".
        url=urljoin(BASE_URL, href.split("?", 1)[0]),
        country=COUNTRY,
    )


def _price(label: str) -> Optional[float]:
    prices = _PRICE_RE.findall(label)
    if not prices:
        return None

    # Last price = including buyer protection, i.e. what you actually pay.
    return float(prices[-1].replace(",", "."))
