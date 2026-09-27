---
name: dealhunt-health
description: Check whether a dealhunt install is actually working, and say what to do if not. Use when the user asks if dealhunt, the deal watcher, the marketplace job or "the hunt" is working or live; when it has been quiet and they wonder why; after a "CRASHED" or "returned nothing" push; or as a routine weekly check. Use it even if they only ask "why haven't I had any alerts?" — silence is the main symptom of a broken source.
---

# dealhunt health check

The failures that matter here are mostly **silent**. A crashed service sends
no alerts, and a blocked source returns zero listings. One broken source ran
for eleven days returning HTTP 200 with the *wrong* listings. "No alerts" can
mean a quiet market or a broken tool, so your job is to tell which, from the
evidence.

Diagnose and recommend. Don't edit files in this skill. Hand real fixes on to
`dealhunt-repair-source` or `dealhunt-triage`.

## 1. Is it running at all?

```bash
systemctl --user list-timers dealhunt.timer --no-pager
systemctl --user is-failed dealhunt.service
journalctl --user -u dealhunt.service --since "-24h" --no-pager -o cat | grep -E "Traceback|Error|Finished|listings seen"
```

- There's no "last" time on the timer: it has never fired. Check it's enabled.
- A traceback in the journal: the process dies before it reaches any source,
  so no source-level warning fires. Read the traceback. Past causes: the
  service ran a different `python3` without the dependencies, and a config
  error after an edit. Run `.venv/bin/dealhunt --dry-run --source tutti` by
  hand to reproduce it.

## 2. Are the sources returning the right listings?

```bash
.venv/bin/dealhunt --report --days 7
```

Read the **Sources** table's `warning` column:

| Warning | Likely cause | Next step |
|---|---|---|
| returning nothing | Blocked, or the site changed | `dealhunt-repair-source` |
| same count N for 6 polls | The site may be answering a different question (a malformed URL returns 200 with unrelated ads) | Run `--sample` for one query and check the titles fit the query. If they do and the source reads fixed pages (hardware_fr does), it's fine. |
| X% of polls blocked | Polling too fast for that site | Raise its `min_interval_min` in `dealhunt.toml` |
| never polled | Disabled, or its interval hasn't elapsed yet | Check `[sources]` |

Also check per-day 403s for a *trend*. Blocking that rises day by day
predicts an outright block:

```bash
journalctl --user -u dealhunt.service --since "-7d" --no-pager -o short-iso | grep -c "403"
```

## 3. Are the results plausible?

In the report:
- **Targets never matched** after a week or more: either nothing is on the
  market, or the patterns are too narrow. Run `--sample` for its queries. If
  real listings for the item appear and don't match, hand on to
  `dealhunt-triage`.
- **Cheapest matches**: parts, empty boxes, auction bids and scams collect
  here. If one would have pushed, hand on to `dealhunt-triage`.

## Report back

Keep it short:

```
dealhunt: <working | degraded | broken>
- <source/target>: <finding> — <evidence, e.g. "0 listings for 13 polls since 00:35">
Next: <the one or two actions, and which skill does them>
```

Only claim it's working if you saw it work: a recent "Finished" run with
non-zero counts from each enabled source. If you're uncertain, say so.
