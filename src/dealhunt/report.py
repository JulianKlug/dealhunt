"""`dealhunt --report`: the facts a maintainer needs, as Markdown.

Reads the store and nothing else, and decides nothing. It exists because
several failures here were silent: a source can answer HTTP 200 with the
wrong listings, and a block can grow a little every day. Both show up only
as a pattern over many polls.

The output is readable by a person, and it is the first thing the
maintenance skills hand to an LLM.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Dict, List

from dealhunt.models import PollRecord
from dealhunt.store.db import Store

# This many polls in a row with one identical, non-zero count is suspicious.
# It can be normal for a source that reads a fixed number of pages.
CONSTANT_RUNS = 6

CHEAPEST_PER_TARGET = 3
RECENT_PUSHES = 10


def render(store: Store, cfg, days: int) -> str:
    polls = store.poll_history(days)

    sections = [
        f"# dealhunt report — last {days} days",
        _sources(polls, cfg),
        _targets(store, cfg),
        _recent_pushes(store, cfg),
        _cheapest(store, cfg),
    ]

    return "\n\n".join(sections) + "\n"


def _sources(polls: List[PollRecord], cfg) -> str:
    by_source: Dict[str, List[PollRecord]] = defaultdict(list)
    for poll in polls:
        by_source[poll.source].append(poll)

    lines = ["## Sources", "", "| source | polls | median count | empty | blocked | warning |", "|---|---|---|---|---|---|"]

    for source in cfg.enabled_sources:
        history = by_source.get(source, [])

        if not history:
            lines.append(f"| {source} | 0 | — | — | — | never polled in this window |")
            continue

        counts = sorted(p.count for p in history)
        empty = sum(1 for p in history if p.count == 0)
        blocked = sum(1 for p in history if p.blocked)

        lines.append(
            f"| {source} | {len(history)} | {counts[len(counts) // 2]} | {empty} "
            f"| blocked {blocked} | {_warning(history)} |"
        )

    return "\n".join(lines)


def _warning(history: List[PollRecord]) -> str:
    """The patterns that have meant trouble before, in order of severity."""
    recent = history[-CONSTANT_RUNS:]

    if all(p.count == 0 for p in recent):
        return "**returning nothing** — blocked or broken"

    if len(recent) == CONSTANT_RUNS and len({p.count for p in recent}) == 1:
        return (f"same count {recent[0].count} for {CONSTANT_RUNS} polls — check the listings "
                "are for the queries asked (normal if it reads fixed pages)")

    blocked_share = sum(1 for p in history if p.blocked) / len(history)
    if blocked_share > 0.25:
        return f"{blocked_share:.0%} of polls blocked — slow it down"

    return ""


def _targets(store: Store, cfg) -> str:
    stored = {row["target_key"]: row for row in store.match_counts()}
    lines = ["## Targets", "", "| target | matches | pushed | price range | note |", "|---|---|---|---|---|"]

    for target in cfg.targets:
        row = stored.get(target.key)

        if row is None:
            lines.append(f"| {target.key} | 0 | 0 | — | never matched: patterns too narrow, or none on the market |")
            continue

        spread = f"{cfg.currency} {row['low']:.0f}–{row['high']:.0f}" if row["low"] is not None else "—"
        lines.append(f"| {target.key} | {row['n']} | {row['pushed']} | {spread} | |")

    return "\n".join(lines)


def _recent_pushes(store: Store, cfg) -> str:
    rows = store.recent_pushes(RECENT_PUSHES)
    lines = ["## Recent pushes", ""]

    if not rows:
        return "\n".join(lines + ["None."])

    for row in rows:
        price = f"{cfg.currency} {row['landed']:.0f}" if row["landed"] is not None else "no price"
        lines.append(f"- {row['notified_at'][:16]} · {row['target_key']} · {price} · [{row['title'][:70]}]({row['url']})")

    return "\n".join(lines)


def _cheapest(store: Store, cfg) -> str:
    lines = ["## Cheapest matches", "", "Parts, bait, bids and scams collect here. Read them.", ""]

    for target in cfg.targets:
        rows = store.cheapest(target.key, CHEAPEST_PER_TARGET)
        if not rows:
            continue

        lines.append(f"**{target.key}**")
        for row in rows:
            bid = " (auction bid)" if row["price_kind"] == "auction" else ""
            lines.append(f"- {cfg.currency} {row['landed']:.0f}{bid} · [{row['title'][:70]}]({row['url']})")
        lines.append("")

    return "\n".join(lines).rstrip()
