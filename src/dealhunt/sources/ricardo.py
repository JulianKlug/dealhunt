"""ricardo.ch — Swiss auction/marketplace site.

The search page is a Next.js app router page: the result set arrives inside the
RSC flight payload (`self.__next_f.push([1,"..."])`) as clean JSON, complete
with a dedicated `size` field. That beats scraping the rendered markup, so we
decode the flight and use the HTML only to recover each article's canonical URL.

Detail pages are bot-protected (403); we never fetch them.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Dict, List, Optional
from urllib.parse import quote

from bs4 import BeautifulSoup

from dealhunt.models import PriceKind, RawListing, SearchQuery, SourceResult
from dealhunt.net.client import HttpError, HttpClient, Transport

log = logging.getLogger(__name__)

SOURCE = "ricardo"
# Where listings ship from; the buyer\'s config decides what that costs.
COUNTRY = "CH"
# Cloudflare challenges python-requests here. Chrome impersonation clears it
# without depending on which curl build is installed; without curl_cffi the
# client falls back to the pinned curl binary, which also used to work.
TRANSPORT = Transport.CHROME
SEARCH_URL = "https://www.ricardo.ch/de/s/{query}/?page={page}"
BASE_URL = "https://www.ricardo.ch"

_FLIGHT_RE = re.compile(r'self\.__next_f\.push\(\[1,\s*(".*?")\]\)', re.S)
_ARTICLES_KEY = '"articles":'


# Sellers list a shipping option per carrier; "get_by_buyer" is local pickup.
_PICKUP_KEY = "get_by_buyer"


def fetch(client: HttpClient, queries: List[SearchQuery]) -> SourceResult:
    result = SourceResult(source=SOURCE)

    for query in queries:
        if _is_blocked(result):
            break

        for page in range(1, query.pages + 1):
            url = SEARCH_URL.format(query=quote(query.text), page=page)

            try:
                html = client.get_text(url)
            except HttpError as exc:
                log.warning("ricardo %r page %d failed: %s", query.text, page, exc)
                result.error = str(exc)

                # A block is a verdict on us, not on this query. Carrying on
                # fires every remaining query into it and prolongs the block.
                if exc.is_block:
                    result.blocked = True

                break
            except Exception as exc:
                log.warning("ricardo %r page %d failed: %s", query.text, page, exc)
                result.error = str(exc)
                break

            result.listings.extend(_parse(html))

    return result


def _parse(html: str) -> List[RawListing]:
    articles = _articles_from_flight(html)
    if not articles:
        return []

    urls = _urls_by_id(html)

    return [_to_listing(a, urls) for a in articles if a.get("id")]


def _articles_from_flight(html: str) -> List[dict]:
    """Reassemble the RSC stream, then pull the `articles` array out of it."""
    chunks = _FLIGHT_RE.findall(html)
    if not chunks:
        return []

    flight = "".join(json.loads(chunk) for chunk in chunks)

    payload = _slice_json_array(flight, _ARTICLES_KEY)
    if payload is None:
        return []

    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        log.warning("ricardo: articles payload did not parse")
        return []


def _slice_json_array(text: str, key: str) -> Optional[str]:
    """Cut out the array that follows `key`, counting brackets.

    A regex cannot do this: the array holds nested objects and arrays, and
    strings inside it contain brackets of their own.
    """
    start = text.find(key)
    if start < 0:
        return None

    start = text.find("[", start)
    if start < 0:
        return None

    depth = 0
    in_string = False
    escaped = False

    for index in range(start, len(text)):
        char = text[index]

        if escaped:
            escaped = False
            continue

        if char == "\\":
            escaped = True
            continue

        if char == '"':
            in_string = not in_string
            continue

        if in_string:
            continue

        if char in "[{":
            depth += 1
        elif char in "]}":
            depth -= 1

            if depth == 0:
                return text[start : index + 1]

    return None


def _urls_by_id(html: str) -> Dict[str, str]:
    """Article hrefs end in the numeric id, so index them by that."""
    soup = BeautifulSoup(html, "lxml")
    urls: Dict[str, str] = {}

    for anchor in soup.select('a[href^="/de/a/"]'):
        href = anchor["href"]
        tail = href.rstrip("/").rsplit("-", 1)

        if len(tail) == 2 and tail[1].isdigit():
            urls[tail[1]] = BASE_URL + href

    return urls


def _to_listing(article: dict, urls: Dict[str, str]) -> RawListing:
    article_id = str(article["id"])

    # Buy-now is what the item costs. A bid is only where an auction stands
    # right now, so it is carried but marked, and never treated as a price.
    if article.get("buyNowPrice") is not None:
        price, kind = article["buyNowPrice"], PriceKind.FIXED
    else:
        price, kind = article.get("bidPrice"), PriceKind.AUCTION

    # `size` and `brand` are separate structured fields — fold them into the
    # description so the matcher sees them like any other listing text.
    extra = " ".join(
        str(article.get(field) or "") for field in ("brand", "size", "conditionKey")
    )

    return RawListing(
        source=SOURCE,
        listing_id=article_id,
        title=article.get("title") or "",
        description=extra.strip(),
        price=float(price) if price is not None else None,
        price_kind=kind,
        currency="CHF",
        shipping_cost=_cheapest_shipping(article),
        url=urls.get(article_id, f"{BASE_URL}/de/a/{article_id}/"),
        location=_location(article),
        country=COUNTRY,
    )


def _cheapest_shipping(article: dict) -> Optional[float]:
    costs = [
        option.get("cost")
        for option in article.get("shipping") or []
        if option.get("key") != _PICKUP_KEY and option.get("cost") is not None
    ]

    return min(costs) if costs else None


def _location(article: dict) -> str:
    for option in article.get("shipping") or []:
        if option.get("city"):
            return str(option["city"])

    return ""


def _is_blocked(result: SourceResult) -> bool:
    return result.blocked
