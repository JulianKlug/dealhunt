---
name: dealhunt-new-target
description: Add a new hunt to dealhunt — turn a description like "a used Fuji X-T4 body", "road bike size 56 under 800" or "any 24 GB GPU" into a tested target file built from real listings. Use whenever the user wants dealhunt (or "the deal watcher") to also look for, track, watch or alert on a new kind of item, add a model to an existing hunt, or asks whether dealhunt could find something.
---

# dealhunt: a new target, from real listings

A target file tells the matcher which listings are the item: `patterns` for
the model, `context` for what kind of thing it is, `exclude` for neighbours
that share the words. The mistake to avoid is writing it from your own
knowledge of the product. The live market is messier: sellers misspell, bundle,
sell spare parts and empty boxes under the model name, and post wanted ads.
Build it from what's actually listed. Field reference: `docs/targets.md`.

## 1. Pin down the brief with the user

- Which models count, and which near-misses don't? ("24 GB" ruled out the
  4080, which is faster at games but has 16 GB.)
- Sized? If so, which scheme (`shoe` exists; a new one goes in
  `matching/sizes.py`). The user's size belongs in *their* `dealhunt.toml`
  `[sizes]`, never in the target file.
- `value` (a fair price) or `cheapest` (the bottom of the market)?

## 2. Look at the real market: a few requests, not a crawl

```bash
.venv/bin/dealhunt --sample --source tutti --query "fuji x-t4"
.venv/bin/dealhunt --sample --source kleinanzeigen --query "fuji x-t4"
```

Two or three queries on the unprotected sources are enough (tutti,
kleinanzeigen, hardware_fr). Leave the Cloudflare-protected ones (ricardo,
ebay_de) to the scheduled job. Read the titles and sort them into: the real
item, parts and accessories, bundles, other models that share words, wanted
ads, and bait.

## 3. Draft `targets/<hunt>.toml`

Put what all its targets share in `[defaults]` (context, accessories,
title_exclude, queries, size_scheme). Then, for each target:

- `patterns`: the model, tolerant of spacing ("x-?t4"), never just the brand.
- `match_scope = "title"` when descriptions ramble about other models or
  hardware.
- `fallback_max`: from the sampled prices, not a guess. Guesses here have
  been wrong by 2×. Calibration replaces it after ~10 days.
- `min_plausible`: when the category attracts scams (electronics especially).

## 4. Tests from the titles you sampled

Create `tests/test_<hunt>.py` like `tests/test_gpu.py`. Real titles that must
match, and real titles that must not (each sorted pile from step 2). Run it
and watch the negative cases fail before you tighten the file.

## 5. Wire it in and watch

- Add the file to `target_files` in the user's `dealhunt.toml`.
- `.venv/bin/python -m pytest -q`, then
  `.venv/bin/dealhunt --dry-run --source tutti` and read what it matches.
- Leave `mode` alone. A new target just collects until the user runs
  `--calibrate`. Tell them to do that after about ten days.

## Report back

List the file, what counts and what's excluded (with an example title for
each exclusion), the sampled price range, and the next step: observe, then
calibrate.
