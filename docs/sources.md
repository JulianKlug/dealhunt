# Adding a marketplace

A source is one module in `src/dealhunt/sources/` that turns a marketplace
into `RawListing`s. It never parses config, never decides whether something
matches, and never touches HTTP directly: it asks `net/client.py` for pages.

## The contract

```python
SOURCE = "example"            # the name used in dealhunt.toml
COUNTRY = "DE"                # where listings ship from (ISO code)
TRANSPORT = Transport.PLAIN   # PLAIN · CURL · CHROME · BROWSER

def fetch(client: HttpClient, queries: list[SearchQuery]) -> SourceResult:
    ...
```

Rules every source follows:

1. **Stop at the first block.** On an `HttpError` with `is_block` (a 403 or
   429), set `result.blocked = True` and return. Sending the remaining
   queries into a block tends to make it last longer.
2. **Let other errors skip only one query.** A timeout is not a block.
3. **Parse on stable, meaningful anchors.** Use embedded JSON, `data-*`
   attributes, ld+json or accessibility labels. Avoid CSS class names, which
   change without notice.
4. **Put the seller's own price in `price`**, and mark auction bids with
   `PriceKind.AUCTION`. A bid is not a price.
5. **Register** the module in `SOURCES` in `cli.py`, and enable it in your
   config.

## Picking a transport: the probe ladder

Try each rung in order, and stop at the first one that returns real listings.

| Rung | Transport | Gets past | Cost |
|---|---|---|---|
| 1 | `PLAIN` (python-requests) | Nothing special | Cheap |
| 2 | `CURL` (curl binary) | Some Cloudflare setups, depending on the curl build | Cheap, fragile |
| 3 | `CHROME` (curl_cffi) | TLS fingerprinting: Cloudflare, eBay | Cheap |
| 4 | `BROWSER` (Playwright) | Listings rendered in JavaScript | ~6 s a page, ~300 MB |
| — | None | DataDome (leboncoin): blocked even a headless browser | — |

**"It returned 200" is not proof it works.** Check that the listings are the
ones you searched for. kleinanzeigen answered a malformed search URL with a
normal-looking page of unrelated ads, and nothing looked wrong for eleven
days.

## Before it goes live

- Save a real page as a test fixture, and pin the parser to it
  (`tests/test_france.py` does this for Vinted and hardware.fr).
- Pin the URL format with a test (`tests/test_sources.py`).
- Run it with `dealhunt --dry-run --source <name>` and read the output.
- Give aggressive sites an interval in `[sources] min_interval_min`.

## Status (as of 2026-09-27)

| Site | Transport | State |
|---|---|---|
| tutti.ch | PLAIN | Reliable |
| ricardo.ch | CHROME | Intermittent Cloudflare blocks, mostly overnight; 2 h interval |
| kleinanzeigen.de | PLAIN | Reliable |
| ebay.de | CHROME | Blocks on volume; 6 h interval, usually one 403 per poll |
| vinted.fr | BROWSER | Reliable so far |
| forum.hardware.fr | CHROME | Reliable |
| leboncoin.fr | — | DataDome. The page, the API, the headless shell and full Chromium all failed. A headed browser on a virtual display is untried. |
| rakuten.fr, backmarket.fr, ebay.fr | — | JavaScript shell or 403; not attempted further |
