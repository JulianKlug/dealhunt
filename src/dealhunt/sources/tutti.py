"""tutti.ch — the largest Swiss classifieds site.

Uses the app's GraphQL API (reverse-engineered; the header set mirrors a
captured Android client, and Node-ish user agents are WAF-blocked). The `body`
field carries the full listing text, which is where sellers put the boot size.
"""

from __future__ import annotations

import logging
import re
from typing import List, Optional

from dealhunt.models import RawListing, SearchQuery, SourceResult
from dealhunt.net.client import HttpClient, Transport

log = logging.getLogger(__name__)

SOURCE = "tutti"
# Where listings ship from; the buyer\'s config decides what that costs.
COUNTRY = "CH"
TRANSPORT = Transport.PLAIN
ENDPOINT = "https://api.tutti.ch/v10/graphql"
LISTING_URL = "https://www.tutti.ch/de/vi/{id}"

# Trimmed from the captured SearchListingsByQuery operation: only the fields we
# actually read. The server accepts an ad-hoc document, so no persisted-query id.
_DOCUMENT = (
    "query SearchListingsByQuery($query: String, $constraints: ListingSearchConstraints, "
    "$cursor: Cursor, $sort: ListingSortMode!, $direction: SortDirection!) "
    "{ searchListingsByQuery(query: $query, constraints: $constraints) "
    "{ listings(after: $cursor, sort: $sort, direction: $direction) { totalCount "
    "edges { node { listingID title body formattedPrice timestamp "
    "postcodeInformation { locationName postcode } } } "
    "pageInfo { hasNextPage endCursor } } } }"
)

_HEADERS = {
    "Content-Type": "application/json; charset=utf-8",
    "X-Tutti-Hash": "11111111-2222-4333-8444-555555555555",
    "X-Tutti-Source": "Android 11.0.0 (40011774)",
    "X-Tutti-Client-Identifier": "android/11.0.0+env-live.git-a9330101d",
    "X-App-Version": "Tutti/11.0.0(40011774)/Android/36",
    "X-App-Id": "",
    "User-Agent": "ch.tutti/android (Google Pixel 7a, OS 16)",
    "Accept-Language": "de",
    "X-APOLLO-OPERATION-NAME": "SearchListingsByQuery",
    "Accept": "application/json",
}

# tutti prices render as "260.-", "1'250.-" or "Gratis".
_PRICE_RE = re.compile(r"[\d'’\.]+")


def fetch(client: HttpClient, queries: List[SearchQuery]) -> SourceResult:
    result = SourceResult(source=SOURCE)

    for query in queries:
        try:
            result.listings.extend(_search(client, query))
        except Exception as exc:
            log.warning("tutti query %r failed: %s", query.text, exc)
            result.error = str(exc)

    return result


def _search(client: HttpClient, query: SearchQuery) -> List[RawListing]:
    listings: List[RawListing] = []
    cursor = None

    for _ in range(query.pages):
        page = _post(client, query.text, cursor)
        listings.extend(_to_listing(edge["node"]) for edge in page["edges"])

        info = page["pageInfo"]
        if not info["hasNextPage"]:
            break

        cursor = info["endCursor"]

    return listings


def _post(client: HttpClient, text: str, cursor: Optional[str]) -> dict:
    payload = {
        "operationName": "SearchListingsByQuery",
        "query": _DOCUMENT,
        "variables": {
            "query": text,
            "constraints": {"intervals": [], "locations": [], "prices": [], "strings": []},
            "cursor": cursor,
            "sort": "TIMESTAMP",
            "direction": "DESCENDING",
        },
    }

    body = client.post_json(ENDPOINT, payload, _HEADERS)

    if body.get("errors"):
        raise RuntimeError(f"graphql: {body['errors']}")

    return body["data"]["searchListingsByQuery"]["listings"]


def _to_listing(node: dict) -> RawListing:
    postcode = node.get("postcodeInformation") or {}

    return RawListing(
        source=SOURCE,
        listing_id=str(node["listingID"]),
        title=node.get("title") or "",
        description=node.get("body") or "",
        price=_parse_price(node.get("formattedPrice")),
        currency="CHF",
        url=LISTING_URL.format(id=node["listingID"]),
        location=postcode.get("locationName") or "",
        country=COUNTRY,
    )


def _parse_price(formatted: Optional[str]) -> Optional[float]:
    if not formatted:
        return None

    match = _PRICE_RE.search(formatted)
    if not match:
        return None

    return float(match.group(0).replace("'", "").replace("’", "").rstrip("."))
