# Keeping it running

## Signals you get for free

| Push | Meaning |
|---|---|
| `dealhunt CRASHED: …` (priority 5) | The process died. Nothing ran. Check the journal. |
| `<source> returned nothing 2x` | The source is blocked or broken. **Silence isn't success.** |
| `N more matches under threshold` | The 12-push cap was hit; the backlog is in the database. |

```bash
journalctl --user -u dealhunt.service -n 60          # the last run
systemctl --user list-timers dealhunt.timer          # is it scheduled?
.venv/bin/dealhunt --dry-run --source <name> -v      # one source, by hand
```

Don't send `--dry-run` requests to a Cloudflare-protected source between
scheduled runs. Your manual requests count toward its rate limit too.

## Failures seen so far, and how each was noticed

| Failure | Silent? | How it was caught |
|---|---|---|
| systemd resolved `python3` to an interpreter without the dependencies | Yes: no alerts, no warnings | Checking whether the timer had ever fired |
| systemd found a different `curl` build, which Cloudflare rejected | Partly | 403s in the journal |
| **kleinanzeigen search URL malformed**: HTTP 200 with the wrong ads | **Yes, for 11 days** | Noticing one source's count never changed |
| ricardo's Cloudflare blocking climbed day by day | Partly | Counting 403s per day in the journal |
| A cost-model change re-pushed 7 listings as "price drops" | No: duplicate pushes | Reading the pushes |
| Auction opening bids read as prices | Yes | Reading the cheapest matches |
| An edit to add a context word silently never applied | Yes | Comparing old and new engines on a real batch |

Several of these were silent, so check every week or two:

- **Per-source listing counts**: a count that never changes means the site is
  answering a question you didn't ask.
- **The cheapest matches per target**, read by a person: that's where the
  scams, parts and bids show up.
- **403s per day per source**: a rising trend means slow down before the site
  blocks you outright.

## Changing the engine safely

Before a change that could alter verdicts, capture a real batch of listings
and diff the old engine's verdicts against the new one's. The restructure
into `dealhunt` did this on 937 listings: identical targets and prices, with
two labels changed, one of which exposed a bug.

## Calibration

`dealhunt --calibrate` rewrites `data/thresholds.json` from observed prices.
Rerun it now and then; prices move with the season. Targets with fewer than
`min_samples` observations keep their `fallback_max`.
