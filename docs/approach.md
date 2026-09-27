# The approach

## The problem

A good second-hand listing is usually gone within hours. Checking five
marketplaces by hand twice a day for months costs more than the item itself.
So a program does the checking, and you only look at what it pushes to you.

## The split: deterministic at run time, LLM-assisted around it

| Phase | What happens | LLM? |
|---|---|---|
| **Run** (every 30 min) | Fetch, match, dedupe, price, push | **Never.** Plain Python. |
| **Design** (once per hunt) | Turn "stiff splitboard boots, EU 43" into model patterns, exclusions, a size scheme, price bars | **Helpful.** Someone has to write the regexes. |
| **Maintain** (ongoing) | Repair a source when its site changes; exclude new kinds of noise from live data | **Needed, or a developer.** This is most of the work. |

The run-time tool doesn't depend on a model. It's cheap, it can be audited,
and it behaves the same at 3 a.m. as at noon. Everything a model would
contribute is written down as a file (a target, a source module, a test) that
the running tool reads.

## How a hunt is set up

1. **Describe the item as a target file.** Model names become `patterns`;
   what kind of item it is (a boot, not a board) becomes `context`; things
   that share its words become `exclude`. See [targets.md](targets.md).
2. **Observe for ~10 days** (`mode = "observe"`). The tool records every
   match and its price, and pushes nothing.
3. **Calibrate.** `dealhunt --calibrate` sets each target's alert bar from the
   prices it observed: the 25th percentile for `value` targets, the 10th for
   `cheapest` targets. The bar comes from the market, not from a guess. The
   guesses on this project were wrong by 2× (a used 3090 turned out to cost
   CHF 1,100, not 700).
4. **Alert** (`mode = "alert"`). Each listing is pushed once, and again only
   when the seller cuts the price.

## What it actually took

Built over three weeks for three hunts (splitboard boots, goggles, a 24 GB
GPU). Roughly:

- **~15 %** writing the pipeline.
- **~25 %** getting past each site's defences: Cloudflare, eBay's TLS
  fingerprinting, and JavaScript-only rendering.
- **~60 %** correcting the matcher against live data. Almost every live run
  surfaced a new kind of false positive: a Barryvox avalanche transceiver
  listed as a ski boot, liners sold as boots, laptops and empty boxes sold as
  GPUs, "RTX 3080 … \*Nicht 3090" (the seller saying it is *not* a 3090),
  wanted ads, auction opening bids read as prices.

Tests pass on imagined data. The live market still contains listings nobody
imagined. So budget for maintenance, and see [operations.md](operations.md)
for how to spot problems.

## Where an LLM helps

Three tasks, each producing a file the backbone reads:

1. **Draft a target file** from a description and a sample of real listings.
2. **Triage false positives**: read the recent pushes, propose `exclude` or
   `accessories` entries, and add a regression test for each.
3. **Repair a source** when a site changes: probe it, find the new shape,
   update the parser, add a test pinned to the real markup.

In every case the result is a reviewed change to a file, with a test. The
model isn't consulted while the tool runs.
