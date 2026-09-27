"""ebay.de — auction + buy-now, ships to CH more often than kleinanzeigen.

eBay rejects any client whose TLS fingerprint is not a real browser: plain
requests gets a flat 403 regardless of headers. So this source requires
curl_cffi and disables itself with one clear log line when it is missing,
rather than hammering an endpoint that will never answer.

    pip install curl_cffi
"""

from __future__ import annotations

import logging
import re
from typing import List, Optional
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

from dealhunt.models import RawListing, SearchQuery, SourceResult
from dealhunt.net.client import HttpError, HAS_CHROME_IMPERSONATION, HttpClient, Transport

log = logging.getLogger(__name__)

SOURCE = "ebay_de"
# Where listings ship from; the buyer\'s config decides what that costs.
COUNTRY = "DE"
TRANSPORT = Transport.CHROME
BASE_URL = "https://www.ebay.de"
# _sop=10 sorts newest first; _ipg caps the page size.
SEARCH_URL = BASE_URL + "/sch/i.html?_nkw={query}&_sop=10&_ipg=60&_pgn={page}"

_PRICE_RE = re.compile(r"(?:EUR|CHF|US \$|\$)\s*([\d.,]+)")
_ITEM_ID_RE = re.compile(r"/itm/(\d+)")

def available() -> bool:
    # The curl-binary fallback is not enough here: eBay wants a Chrome
    # fingerprint specifically.
    return HAS_CHROME_IMPERSONATION


def fetch(client: HttpClient, queries: List[SearchQuery]) -> SourceResult:
    result = SourceResult(source=SOURCE)

    if not available():
        result.error = "curl_cffi not installed — source skipped"
        log.warning("ebay_de disabled: %s", result.error)
        return result

    for query in queries:
        if _is_blocked(result):
            break

        for page in range(1, query.pages + 1):
            url = SEARCH_URL.format(query=quote_plus(query.text), page=page)

            try:
                html = client.get_text(url)
            except HttpError as exc:
                log.warning("ebay_de %r page %d failed: %s", query.text, page, exc)
                result.error = str(exc)

                # A block is a verdict on us, not on this query. Carrying on
                # fires every remaining query into it and prolongs the block.
                if exc.is_block:
                    result.blocked = True

                break
            except Exception as exc:
                log.warning("ebay_de %r page %d failed: %s", query.text, page, exc)
                result.error = str(exc)
                break

            result.listings.extend(_parse(html))

    return result


def _parse(html: str) -> List[RawListing]:
    soup = BeautifulSoup(html, "lxml")
    listings = []
    seen = set()

    # eBay renames its wrapper classes often, so key off the item link instead.
    for anchor in soup.select('a[href*="/itm/"]'):
        listing = _to_listing(anchor)

        if listing is None or listing.listing_id in seen:
            continue

        seen.add(listing.listing_id)
        listings.append(listing)

    return listings


def _to_listing(anchor) -> Optional[RawListing]:
    match = _ITEM_ID_RE.search(anchor.get("href", ""))
    if not match:
        return None

    title = anchor.get_text(" ", strip=True)
    if not title:
        return None

    # Price lives in a sibling within the shared list-item container.
    container = anchor.find_parent("li") or anchor.parent
    text = container.get_text(" ", strip=True) if container else title

    return RawListing(
        source=SOURCE,
        listing_id=match.group(1),
        title=title,
        description=text,
        price=_parse_price(text),
        currency="EUR",
        url=f"{BASE_URL}/itm/{match.group(1)}",
        country=COUNTRY,
    )


def _parse_price(text: str) -> Optional[float]:
    match = _PRICE_RE.search(text)
    if not match:
        return None

    # German formatting: "1.234,56" -> 1234.56
    raw = match.group(1).replace(".", "").replace(",", ".")

    try:
        return float(raw)
    except ValueError:
        return None


def _is_blocked(result: SourceResult) -> bool:
    return result.blocked
