# Working on dealhunt (for coding agents)

dealhunt watches second-hand marketplaces and pushes good-value listings. The
**runtime is deterministic Python on a timer and never calls a model.** You are
here to *maintain* it. Your output is a reviewed change to a file, with a test,
never a runtime dependency on yourself.

## Invariants: don't break these

1. **No model at run time.** Nothing under `src/dealhunt/` may call an LLM,
   an API for one, or read your output while the tool runs.
2. **Layers talk only to the layer below.** `cli` → `sources` / `matching` /
   `store` / `notify`; only `net/client.py` does HTTP; `matching/` is pure (no
   I/O). A source never decides whether something matches.
3. **Personal data stays personal.** `dealhunt.toml` and `data/` are
   gitignored. Never commit them, and never copy a user's size, address or
   topic into `targets/*.toml`. Target files must stay shareable.
4. **Be polite to the sites.** One request to check a hypothesis, not a loop.
   Sources stop at the first 403/429, and so should you. Don't `--dry-run` a
   Cloudflare-protected source (ricardo, ebay_de) between scheduled runs;
   your requests count toward the same limit.
5. **Test first, from real data.** Every fix starts as a failing test built
   from the real title, page or URL that exposed the problem. Tests built on
   imagined listings have repeatedly missed what the live market does.

## Commands

```bash
.venv/bin/python -m pytest -q                 # all tests (~0.5 s)
.venv/bin/dealhunt --report                   # facts: polls, targets, pushes, cheapest
.venv/bin/dealhunt --explain "TITLE" [--description D --country DE --price 150]
.venv/bin/dealhunt --sample --source tutti --query "rtx 3090"   # one request, JSON
.venv/bin/dealhunt --dry-run --source NAME    # full poll of one source, no writes
journalctl --user -u dealhunt.service -n 60   # the last scheduled run
```

## Where things live

| To change… | Edit | Not |
|---|---|---|
| What counts as an item, spare parts, exclusions | `targets/*.toml` | the engine |
| Condition / wanted-ad / "comes with" words (any item, DE/FR/EN) | `matching/rules.py` | a target file |
| How a size is read | `matching/sizes.py` (a scheme) | a target |
| How a site is fetched or parsed | `sources/<name>.py` | the matcher |
| Rate limits, TLS, browser | `net/client.py` | a source |

## Verifying a change

- `pytest -q` green, including your new test.
- `--explain` on the listing that prompted the change shows the new decision.
- **Nothing genuine is lost.** For matcher or target changes, run the
  engine's verdicts on a real batch (`--sample` a few queries) before and
  after, and diff them. Every difference must be one you meant.
  `docs/operations.md` → "Changing the engine safely".
- Leave committing to the user unless asked. Commit messages explain *why*
  and never mention the assistant.

## Maintenance skills

`.claude/skills/` holds the four jobs this repo needs, one skill each:
`dealhunt-health` (is it working?), `dealhunt-triage` (a bad push),
`dealhunt-new-target` (a new hunt), `dealhunt-repair-source` (a site changed).
Other agents can read them as procedures. Background: `docs/`.
