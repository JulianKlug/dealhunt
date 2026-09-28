# dealhunt

Watches second-hand marketplaces for items you describe, and pushes the good
deals to your phone. Anything that can be described by its name and a fair
price: boots in your size, an RTX 5090, a specific camera lens.

It runs on a timer as plain, deterministic Python: no model is involved in the
running tool. An LLM is useful for writing target files and for repairing a
source when a site changes, which is described in
[docs/approach.md](docs/approach.md).

```
        systemd timer, every 30 min
                   │
                   ▼
  ┌──────────────────────────────────┐
  │ cli        orchestration only    │
  └──────────────────────────────────┘
      │           │          │         │
      ▼           ▼          ▼         ▼
   sources/    matching/   store/    notify/
   one per     pure         sqlite:   ntfy.sh
   marketplace verdicts     dedupe,   push
      │                     prices
      ▼
   net/        the only code that talks HTTP:
               plain · curl · Chrome TLS · headless browser
```

## Quick start

```bash
git clone … && cd dealhunt
PYTHON=python3 deploy/install.sh --no-timer   # venv, deps, browser, systemd units
$EDITOR dealhunt.toml                        # who you are: home, sizes, alert topic
.venv/bin/dealhunt --notify-test             # does the push reach your phone?
.venv/bin/dealhunt --dry-run                 # what would it find right now?
systemctl --user enable --now dealhunt.timer
```

After ~10 days in `observe` mode:

```bash
.venv/bin/dealhunt --calibrate               # alert bars from observed prices
# then set  mode = "alert"  in dealhunt.toml
```

## Two kinds of config

| File | Holds | Shared? |
|---|---|---|
| `dealhunt.toml` | **You:** home country, delivery addresses, import VAT, sizes, alert topic | No (gitignored) |
| `targets/*.toml` | **What to hunt:** item patterns, exclusions, price bars, search queries | Yes, as-is |

A target file contains nothing personal, so a good one can be passed around:
sizes live in your config, so the same `splitboard-boots.toml` works for size 39
and size 46. See [docs/targets.md](docs/targets.md).

## Sources

| Source | Country | Transport | Notes |
|---|---|---|---|
| tutti.ch | CH | plain | App GraphQL API; full listing body |
| ricardo.ch | CH | Chrome TLS | Next.js RSC payload; Cloudflare, so polled every 2 h |
| kleinanzeigen.de | DE | plain | HTML + embedded ld+json |
| ebay.de | DE | Chrome TLS | Blocks on volume even so; polled every 6 h |
| vinted.fr | FR | headless browser | Renders in JavaScript; polled every 2 h |
| forum.hardware.fr | FR | Chrome TLS | GPU sale topics; price in the title |
| leboncoin.fr | FR | — | **Not supported**: DataDome blocks every approach tried |

Adding one: [docs/sources.md](docs/sources.md).

## Keeping it running

Sites change, and the failures are often silent: a malformed search URL can
return HTTP 200 with the wrong results. Three read-only commands give you the
facts:

```bash
.venv/bin/dealhunt --report                 # polls, blocks, targets, pushes, cheapest matches
.venv/bin/dealhunt --explain "TITLE"        # why a listing matched or was dropped
.venv/bin/dealhunt --sample --source tutti --query "rtx 3090"   # real listings as JSON
```

With a coding agent, four skills in `.claude/skills/` take it from there:
`dealhunt-health`, `dealhunt-triage`, `dealhunt-new-target` and
`dealhunt-repair-source`. Their shared rules are in [AGENTS.md](AGENTS.md).
[docs/operations.md](docs/operations.md) lists what has broken so far, and how
each failure was noticed.

## Layout

```
dealhunt.example.toml     copy to dealhunt.toml
targets/                  shareable hunts
src/dealhunt/
  cli.py                  orchestration: fetch → match → store → notify
  config.py               TOML → typed objects; validates up front
  models.py               the types every layer speaks
  net/client.py           HTTP, rate limits, retries, 4 transports
  sources/                one module per marketplace
  matching/rules.py       verdicts + the generic DE/FR/EN vocabulary
  matching/sizes.py       sizing schemes ("shoe")
  store/db.py             sqlite, with in-place migrations
  notify/ntfy.py          push notifications
deploy/                   installer + systemd templates
docs/                     approach · targets · sources · operations
AGENTS.md, .claude/skills/  LLM maintenance: rules + 4 entry points
tests/                    run against the shipped example targets
```

## Caveats

- **Buying second-hand is the risky part.** The tool finds candidates. It can't
  judge a seller, and it flags implausibly cheap prices without catching every
  scam. Pay with buyer protection, never by bank transfer to a stranger.
- **Scraping is against most marketplaces' terms.** dealhunt polls slowly, for
  one person, and stops a source at its first block. Keep it that way.
- **Import costs are estimates.** Check the current rules before a large
  purchase.
