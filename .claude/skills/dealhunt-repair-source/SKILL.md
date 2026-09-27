---
name: dealhunt-repair-source
description: Repair a dealhunt marketplace source after its site changed or started blocking — zero listings, a "returned nothing 2x" push, HTTP 403s in the journal, listings that no longer fit the query, or a parser error. Also use to add a new marketplace to dealhunt. Use whenever the user says a site (tutti, ricardo, kleinanzeigen, ebay, vinted, hardware.fr, leboncoin, or a new one) stopped working, is blocked, or should be added.
---

# dealhunt: repair (or add) a source

A source turns a marketplace into listings, and nothing else. It's also where
dealhunt breaks most often, because sites change without warning. Contract and
background: `docs/sources.md`.

**Be polite while you debug.** Every request you send counts toward the same
rate limit the scheduled job lives under. A block is a verdict on your IP, and
hammering tends to make it last longer. One request per hypothesis. When you
get a 403, stop and think before sending another.

## 1. Characterise the failure, from what's already recorded

```bash
.venv/bin/dealhunt --report --days 7          # when did it start? all polls, or some?
journalctl --user -u dealhunt.service --since "-3d" --no-pager -o cat | grep "<source>"
```

| Symptom | Usually |
|---|---|
| 403/429 on every poll, starting suddenly | New or stricter bot protection → step 3 (transport) |
| 403 on some polls, rising over days | Rate limiting → raise `min_interval_min` first; no code change |
| HTTP 200, zero listings | The markup or JSON changed → step 4 (parser) |
| HTTP 200, plausible count, wrong listings | The URL format changed. The site answers the old URL with something else. |
| Traceback in the parser | The shape changed → step 4 |

## 2. Reproduce with one request

```bash
.venv/bin/dealhunt --sample --source <name> --query "<a query from its targets>"
```

**Check that the listings fit the query**, not just that there are some.
That's the check that would have caught the kleinanzeigen URL bug in days,
not eleven.

## 3. If blocked: climb the probe ladder

`PLAIN` → `CURL` → `CHROME` (curl_cffi TLS impersonation) → `BROWSER`
(headless Chromium). Stop at the first rung that returns real listings, and
set it as the source's `TRANSPORT`. Probe outside the source first with a
small script through `net/client.py`, so the code under test is the code
that runs. DataDome (leboncoin) beat every rung, so if a site uses it, say
so rather than escalating.

## 4. If the shape changed: fix the parser against a real page

1. Save one real response as a fixture (trim it to a few items; no personal
   data).
2. Write the failing test first: parse the fixture, and assert id, title,
   price, URL and country (see `tests/test_france.py`). Pin the URL format
   too (`tests/test_sources.py`).
3. Anchor the parser on meaning, not CSS classes: embedded JSON, `data-*`
   attributes, ld+json, accessibility labels. Classes are what changes.
4. Keep the rules: stop at the first block (`result.blocked = True`), let
   other errors skip only one query, put the seller's own price in `price`,
   and mark auction bids `PriceKind.AUCTION`.

## 5. Verify under the real service

```bash
.venv/bin/python -m pytest -q
.venv/bin/dealhunt --dry-run --source <name>      # once
systemctl --user start dealhunt.service && journalctl --user -u dealhunt.service -n 20 --no-pager
```

The systemd run matters. Two past bugs existed only under systemd (a
different `python3`, a different `curl`). Then update the status table in
`docs/sources.md` with today's date.

## Report back

State the symptom and since when, the cause (with the evidence), the change,
the test, and the first real poll's count. If it can't be fixed (for example
DataDome), say so plainly, and suggest disabling the source in
`dealhunt.toml` rather than leaving it failing.
