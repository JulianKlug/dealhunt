"""forum.hardware.fr, Achats & Ventes / Hardware — France's used-GPU market.

A forum, not a marketplace: each sale is a topic, with the price usually in
the title ("[VDS - 75] RTX 3090 FE 750€", where 75 is the department). There
is no search to hit, so this source reads the newest topic pages and ignores
the query list; the title-only GPU target does the filtering.

Topic tags: [VDS] sale, [ACH] wanted, [ECH] swap, [DON] giveaway. Only sales
are kept.
"""

from __future__ import annotations

import html as html_lib
import logging
import re
from typing import List, Optional

from dealhunt.models import RawListing, SearchQuery, SourceResult
from dealhunt.net.client import HttpError, HttpClient, Transport

log = logging.getLogger(__name__)

SOURCE = "hardware_fr"
# Where listings ship from; the buyer\'s config decides what that costs.
COUNTRY = "FR"
TRANSPORT = Transport.CHROME
BASE_URL = "https://forum.hardware.fr"
TOPIC_LIST_URL = BASE_URL + "/hfr/AchatsVentes/Hardware/liste_sujet-{page}.htm"

# The newest ~150 sale topics. The forum moves at a few pages a day.
PAGES = 3

_TOPIC_RE = re.compile(r'<a href="(/hfr/AchatsVentes/Hardware/[^"]+)" class="cCatTopic"[^>]*>([^<]+)</a>')
_TOPIC_ID_RE = re.compile(r"sujet_(\d+)")
_SALE_TAG_RE = re.compile(r"^\s*\[\s*VDS", re.I)
_DEPARTMENT_RE = re.compile(r"^\s*\[\s*VDS\s*-\s*(\d{2,3}|2[AB])\s*\]", re.I)

# "750€", "750 €", "1 450 euros", "1.450 eur"
_PRICE_RE = re.compile(r"(\d{1,3}(?:[ .]\d{3})+|\d+)\s*(?:€|eur(?:o?s?)?\b)", re.I)


def fetch(client: HttpClient, queries: List[SearchQuery]) -> SourceResult:
    result = SourceResult(source=SOURCE)

    for page in range(1, PAGES + 1):
        try:
            html = client.get_text(TOPIC_LIST_URL.format(page=page))
        except HttpError as exc:
            log.warning("hardware_fr page %d failed: %s", page, exc)
            result.error = str(exc)
            result.blocked = exc.is_block
            break

        result.listings.extend(parse(html))

    return result


def parse(html: str) -> List[RawListing]:
    listings = []

    for href, raw_title in _TOPIC_RE.findall(html):
        title = html_lib.unescape(raw_title).strip()

        if not _SALE_TAG_RE.search(title):
            continue

        topic = _TOPIC_ID_RE.search(href)
        if not topic:
            continue

        listings.append(
            RawListing(
                source=SOURCE,
                listing_id=topic.group(1),
                title=title,
                price=_price(title),
                currency="EUR",
                url=BASE_URL + href,
                location=_department(title),
                country=COUNTRY,
            )
        )

    return listings


def _price(title: str) -> Optional[float]:
    match = _PRICE_RE.search(title)
    if not match:
        return None

    return float(re.sub(r"[ .]", "", match.group(1)))


def _department(title: str) -> str:
    match = _DEPARTMENT_RE.search(title)

    return f"dept {match.group(1)}" if match else ""
