"""Source behaviour under blocking.

A 403 from Cloudflare is a verdict on us, not on one query. Firing the rest
of the run's queries into it only extends the block.
"""

from __future__ import annotations

import sys
from pathlib import Path


from dealhunt.models import SearchQuery  # noqa: E402
from dealhunt.net.client import HttpError  # noqa: E402
from dealhunt.sources import kleinanzeigen, ricardo  # noqa: E402

HTTP_FORBIDDEN = 403

QUERIES = [SearchQuery(text=q, pages=2) for q in ("a", "b", "c", "d")]


class BlockedClient:
    def __init__(self):
        self.calls = 0

    def get_text(self, url, headers=None):
        self.calls += 1
        raise HttpError(f"GET {url}: HTTP {HTTP_FORBIDDEN}", status_code=HTTP_FORBIDDEN)


class FlakyClient:
    """A timeout is not a block: the next query deserves its chance."""

    def __init__(self):
        self.calls = 0

    def get_text(self, url, headers=None):
        self.calls += 1
        raise HttpError(f"GET {url}: timed out")


def test_ricardo_stops_the_whole_run_on_a_block():
    client = BlockedClient()
    result = ricardo.fetch(client, QUERIES)

    assert client.calls == 1
    assert result.listings == []
    assert result.error


def test_kleinanzeigen_stops_the_whole_run_on_a_block():
    client = BlockedClient()
    kleinanzeigen.fetch(client, QUERIES)

    assert client.calls == 1


def test_a_transient_failure_does_not_abort_the_other_queries():
    client = FlakyClient()
    ricardo.fetch(client, QUERIES)

    assert client.calls == len(QUERIES)


def test_query_order_rotates_so_blocks_do_not_starve_the_same_queries():
    from dealhunt import cli as run

    queries = ["a", "b", "c", "d"]
    slot = run.ROTATION_SLOT_S
    starts = {run.rotate(queries, i * slot)[0] for i in range(len(queries))}

    assert starts == set(queries)
    assert sorted(run.rotate(queries, 7 * slot)) == queries


# --- kleinanzeigen URL format ----------------------------------------------
# The site answers a malformed search path with a plausible page of unrelated
# ads and HTTP 200, so a wrong URL fails silently. These pin the real format,
# verified against the live site: page 1 has no "seite" segment, and later
# pages carry the slug without its "s-" prefix.

def test_kleinanzeigen_first_page_url():
    assert kleinanzeigen.search_url("snowboard boots", 1) == (
        "https://www.kleinanzeigen.de/s-snowboard-boots/k0"
    )


def test_kleinanzeigen_later_page_url():
    assert kleinanzeigen.search_url("snowboard boots", 2) == (
        "https://www.kleinanzeigen.de/s-seite:2/snowboard-boots/k0"
    )


def test_kleinanzeigen_slug_normalises_punctuation():
    assert kleinanzeigen.search_url("Snowboard Boots 43!", 1) == (
        "https://www.kleinanzeigen.de/s-snowboard-boots-43/k0"
    )


# --- header consistency under impersonation --------------------------------

def test_impersonation_keeps_its_own_user_agent():
    """A forced UA that disagrees with the impersonated TLS fingerprint is a
    bot signal; plain transports still need one, since they have no other."""
    from dealhunt.net.client import Transport, headers_for

    assert "User-Agent" not in headers_for(Transport.CHROME)
    assert "User-Agent" in headers_for(Transport.PLAIN)
    assert "User-Agent" in headers_for(Transport.CURL)


def test_ricardo_uses_chrome_impersonation():
    from dealhunt.net.client import Transport

    assert ricardo.TRANSPORT is Transport.CHROME
