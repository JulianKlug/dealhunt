"""kleinanzeigen.de — by far the deepest German second-hand pool.

The site was rebuilt on utility CSS classes, so class-based selectors rot
quickly. We anchor on things that carry meaning instead:
  - `article[data-adid]`   the listing container and its id
  - `data-href`            the canonical URL
  - the embedded ld+json   title and (truncated) description
  - a "… €" regex over the article text for the price

Many sellers ship only within Germany; the matcher flags rather than drops
those, because "Versand nach CH?" is one message away.
"""

from __future__ import annotations

import json
import logging
import re
from typing import List, Optional
from urllib.parse import quote

from bs4 import BeautifulSoup

from dealhunt.models import RawListing, SearchQuery, SourceResult
from dealhunt.net.client import HttpError, HttpClient, Transport

log = logging.getLogger(__name__)

SOURCE = "kleinanzeigen"
# Where listings ship from; the buyer\'s config decides what that costs.
COUNTRY = "DE"
TRANSPORT = Transport.PLAIN
BASE_URL = "https://www.kleinanzeigen.de"
FIRST_PAGE_URL = BASE_URL + "/s-{slug}/k0"
LATER_PAGE_URL = BASE_URL + "/s-seite:{page}/{slug}/k0"

# "1.250 €" — German thousands separator, price always precedes the symbol.
_PRICE_RE = re.compile(r"([\d.]+)\s*€")
_POSTCODE_RE = re.compile(r"\b(\d{5})\s+([A-ZÄÖÜ][^\d]{2,40}?)\s+(?:Heute|Gestern|\d{2}\.)")


def fetch(client: HttpClient, queries: List[SearchQuery]) -> SourceResult:
    result = SourceResult(source=SOURCE)

    for query in queries:
        if _is_blocked(result):
            break

        for page in range(1, query.pages + 1):
            url = search_url(query.text, page)

            try:
                html = client.get_text(url)
            except HttpError as exc:
                log.warning("kleinanzeigen %r page %d failed: %s", query.text, page, exc)
                result.error = str(exc)

                # A block is a verdict on us, not on this query. Carrying on
                # fires every remaining query into it and prolongs the block.
                if exc.is_block:
                    result.blocked = True

                break
            except Exception as exc:
                log.warning("kleinanzeigen %r page %d failed: %s", query.text, page, exc)
                result.error = str(exc)
                break

            result.listings.extend(_parse(html))

    return result


def search_url(text: str, page: int) -> str:
    """Build a search URL in the site's real format.

    Getting this wrong fails silently: a malformed path still returns HTTP 200
    with a page of unrelated ads. Page 1 has no "seite" segment; later pages
    carry the bare slug, e.g.
        /s-snowboard-boots/k0
        /s-seite:2/snowboard-boots/k0
    """
    slug = quote(re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-"))

    if page == 1:
        return FIRST_PAGE_URL.format(slug=slug)

    return LATER_PAGE_URL.format(page=page, slug=slug)


def _parse(html: str) -> List[RawListing]:
    soup = BeautifulSoup(html, "lxml")

    return [
        listing
        for article in soup.select("article[data-adid]")
        for listing in [_to_listing(article)]
        if listing is not None
    ]


def _to_listing(article) -> Optional[RawListing]:
    ad_id = article.get("data-adid")
    href = article.get("data-href")

    if not ad_id or not href:
        return None

    text = article.get_text(" ", strip=True)
    title, description = _from_ld_json(article)

    return RawListing(
        source=SOURCE,
        listing_id=str(ad_id),
        title=title or text[:120],
        description=description,
        price=_parse_price(text),
        currency="EUR",
        url=BASE_URL + href,
        location=_parse_location(text),
        country=COUNTRY,
    )


def _from_ld_json(article) -> tuple:
    """Each article embeds an ImageObject carrying the real title and blurb."""
    for script in article.select('script[type="application/ld+json"]'):
        if not script.string:
            continue

        try:
            data = json.loads(script.string)
        except json.JSONDecodeError:
            continue

        if data.get("title"):
            return data.get("title", ""), data.get("description", "")

    return "", ""


def _parse_price(text: str) -> Optional[float]:
    match = _PRICE_RE.search(text)
    if not match:
        return None

    return float(match.group(1).replace(".", ""))


def _parse_location(text: str) -> str:
    match = _POSTCODE_RE.search(text)

    return f"{match.group(1)} {match.group(2).strip()}" if match else ""


def _is_blocked(result: SourceResult) -> bool:
    return result.blocked
