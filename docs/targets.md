# Writing a target file

A target file describes items worth hunting. It contains nothing personal:
your size, your country and your alert topic live in `dealhunt.toml`.

```toml
[defaults]                      # optional; applies to every target in the file
context = ["brille", "goggle", "masque"]
queries = ["skibrille", "ski goggles"]

[[targets]]
key = "smith-squad"             # stable id: used in the database and thresholds
label = "Smith Squad"           # shown in the push notification
patterns = ["smith.{0,12}squad"]
fallback_max = 80               # alert bar, in your currency, until calibrated
```

A target's own value replaces the file default. Lists are not merged.

## Fields

| Field | Meaning |
|---|---|
| `patterns` | Regexes; **any** hit means "this is the item". |
| `context` | Regexes; **one must also** hit. Says what kind of thing it is: a board named like a boot is not a boot. |
| `exclude` | Regexes; **any** hit disqualifies. For neighbouring things that share the words. |
| `title_exclude` | Like `exclude`, but only ever reads the title ("junior", "kids"). |
| `accessories` | Spare parts sold under the item's name ("Ersatzglas", "lens only"). Read from the title, and forgiven when the title says the item comes *with* the part ("inkl. Ersatzglas"). |
| `match_scope` | `full` (default) reads title and description. `title` reads the title only. |
| `size_scheme` | Omit for unsized items. `"shoe"` makes the size mandatory, checked against `[sizes.shoe]` in your config. |
| `price_policy` | `value` (default): alert at a fair price, the 25th percentile. `cheapest`: only the bottom of the market, the 10th percentile. |
| `fallback_max` | Alert bar until calibration has at least `min_samples` prices. |
| `min_plausible` | Below this, a fixed price is flagged, never pushed, and kept out of calibration. It's almost always a part, bait or a scam. |
| `plausible_floors` | Stricter floors for pricier models within a target, by title regex: `{ "4090" = 1000 }`. |
| `queries` | Search terms that find this target. Merged across all targets and deduplicated. |

## Pitfalls, each learned from a real listing

- **Match against the title when descriptions ramble.** GPU sellers write "faster than my old 3090" and "was in my PC". With `full` scope, the first makes a 4080 look like a 3090 and the second excludes a genuine card. Use `match_scope = "title"`.
- **`context` catches most false positives.** "Burton Photon" is a boot, but the same brand sells boards and bindings. Requiring a boot word removes those.
- **Watch out for words inside other words.** "Hard**boot**" contains "boot", so heel risers matched as boots. Add an `exclude`.
- **Watch for negations.** "RTX 3080 20GB … \*Nicht 3090" ("not a 3090") names the model it isn't. If a smaller model is named, exclude it.
- **Don't put sizes in queries.** "snowboard boots 43" belongs in your own `[search] extra_queries`, not in a shared file.
- **Don't trust your first price guess.** Start in `observe` mode and let calibration set the bar.

## Checking a target

Write a test first, from real titles. `tests/test_gpu.py` is the model to
follow: one test per real false positive, and one per genuine listing that
must keep matching.

```bash
pytest tests/ -q
.venv/bin/dealhunt --dry-run --source tutti    # see it against the live market
```
