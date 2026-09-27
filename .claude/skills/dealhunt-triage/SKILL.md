---
name: dealhunt-triage
description: Fix a dealhunt false positive or false negative with a regression test — a push that should never have been sent (a spare part, an empty box, a laptop instead of a GPU, a wanted ad, a wrong size, a scam price) or a genuine listing that was missed. Use whenever the user forwards or describes a bad dealhunt alert, says "why did it send me this", "this isn't a <item>", "it missed this listing", or asks to review recent pushes for noise.
---

# dealhunt triage: one bad verdict → one test → the smallest fix

Most of dealhunt's maintenance has been this job. Every live run surfaced
listings nobody had imagined: a Barryvox avalanche transceiver matched as a
ski boot, "RTX 3080 … \*Nicht 3090" (the seller saying it is *not* a 3090),
heel risers matched as boots because "Hard**boot**" contains "boot". The
discipline that works: the real listing becomes a test first, then you make
the narrowest change that fixes it, then you prove nothing genuine was lost.

## 1. Get the real listing

Use the exact title, and the description if the target uses `full` scope,
from the push or from `.venv/bin/dealhunt --report` (Recent pushes, Cheapest
matches). Don't paraphrase it: the bug is usually in the exact wording.

## 2. See why the matcher decided what it did

```bash
.venv/bin/dealhunt --explain "EXACT TITLE" --description "BODY" --country DE --price 1500
```

The trace names the pattern that fired and the context, exclude or size check
that let it through or dropped it.

## 3. Name the failure, and fix it at the right level

| Failure | Where the fix goes |
|---|---|
| A different item that shares a word ("Hard**boot**", a board named like a boot) | That target file's `exclude` |
| A spare part sold under the item's name (lens, liner, box) | The file's `accessories`. It already forgives "inkl./mit/+" |
| A different size of the same product (junior, kids) | `title_exclude` |
| The description mentions other models or hardware ("faster than my old 3090") | `match_scope = "title"` |
| A negation or a lesser model ("\*Nicht 3090", "3080") | `exclude` of the lesser models |
| A price too good to be real | `min_plausible` / `plausible_floors` |
| A wanted ad, broken, or heavily used, in *any* language, for *any* item | `matching/rules.py` vocabulary, with care, because it affects every target |
| A size misread ("275" = 27.5, "NP ca. 280" is money) | `matching/sizes.py` |
| A genuine listing missed | Widen `patterns` / `context`, or add `queries` |

Prefer the target file: it keeps the fix local. Touch the engine only when
the word is genuinely item-independent.

## 4. Test first

Add the real title to the matching test file (`tests/test_gpu.py`, or a new
`tests/test_<target>.py`), following the existing style: one test per real
false positive, named after what it proves. Also include a genuine listing
that must *keep* matching, next to it. Run it, and **watch it fail** for the
right reason before you change anything.

## 5. Fix, then prove nothing genuine was lost

```bash
.venv/bin/python -m pytest -q
.venv/bin/dealhunt --explain "EXACT TITLE" ...        # now dropped (or now matched)
```

An `exclude` is a blunt tool. Before and after the change, run the verdicts
on a real batch for the affected target's queries (`--sample` two or three
queries, once each) and diff them. Every newly dropped genuine listing is a
regression. Narrow the regex until only the bad ones move.

## Report back

Describe the listing, why it got through (from the trace), the change and why
it goes at that level, and the diff result ("N real listings: only the 2 bad
ones changed"). Leave committing to the user.
