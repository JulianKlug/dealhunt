#!/usr/bin/env python3
"""dealhunt — poll marketplaces for good-value items, push the ones worth seeing.

One pass per invocation; a systemd timer supplies the repetition. Orchestration
only: it walks sources, hands each listing to the matcher, records the verdict
and decides whether to push. It knows nothing about HTML, GraphQL or SQL.

    python3 run.py                     one poll, honouring config mode
    python3 run.py --dry-run           poll and print, push nothing, write nothing
    python3 run.py --source tutti      restrict to one source
    python3 run.py --calibrate         set alert thresholds from observed prices
    python3 run.py --notify-test       prove the ntfy topic reaches your phone
"""

from __future__ import annotations

import argparse
import logging
import statistics
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from dealhunt import config
from dealhunt.matching.rules import evaluate
from dealhunt.models import Event, Match, Mode, Outcome, PriceKind, PricePolicy, SourceResult
from dealhunt.net.client import HttpClient, Transport
from dealhunt.notify.ntfy import Notifier
from dealhunt.sources import ebay_de, hardware_fr, kleinanzeigen, ricardo, tutti, vinted
from dealhunt.store.db import Store

log = logging.getLogger("dealhunt")

# The buyer's config. Relative paths inside it resolve against its own folder.
DEFAULT_CONFIG = Path("dealhunt.toml")

# Sources keyed by the name used in config.toml. Each module declares the
# transport it needs; we build one client per transport and share it.
SOURCES = {
    tutti.SOURCE: tutti,
    ricardo.SOURCE: ricardo,
    kleinanzeigen.SOURCE: kleinanzeigen,
    ebay_de.SOURCE: ebay_de,
    vinted.SOURCE: vinted,
    hardware_fr.SOURCE: hardware_fr,
}

# Two dead polls in a row means the site changed, not that the market is quiet.
EMPTY_RUNS_BEFORE_WARNING = 2

# The first alert run after observing clears a backlog. Push the best of it and
# summarise the rest, rather than firing fifty notifications at once.
MAX_ALERTS_PER_RUN = 12

# Sources stop at the first block, so whatever sits late in the query list is
# what a block skips. Rotating the start each timer slot spreads that loss
# evenly instead of always starving the same queries.
ROTATION_SLOT_S = 30 * 60

# The timer fires with up to 5 minutes of random delay. Without slack, a poll
# landing at 116 of 120 minutes would be skipped and ricardo would drift to
# every 2.5 hours.
TIMER_JITTER_SLACK = timedelta(minutes=5)


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    cfg = config.load(args.config)
    notifier = Notifier(cfg.ntfy_topic, cfg.currency, enabled=not args.dry_run)

    if args.notify_test:
        notifier.test()
        return 0

    if args.report_failure:
        notifier.failure(args.report_failure)
        return 0

    store = Store(cfg.db_path)

    try:
        if args.calibrate:
            return _calibrate(cfg, store)

        return _poll(cfg, store, notifier, args)
    finally:
        store.close()


def _poll(cfg, store: Store, notifier: Notifier, args) -> int:
    mode = Mode.OBSERVE if args.dry_run else cfg.mode
    thresholds = _thresholds(cfg)
    clients: Dict[Transport, HttpClient] = {}

    outcomes: Counter = Counter()
    seen_this_run = set()
    to_alert: List[Tuple[Match, Event]] = []

    for name in _selected_sources(cfg, args.source):
        module = SOURCES[name]

        # A source that cannot run at all is skipped outright: building its
        # client would log a misleading fallback, and counting its empty result
        # would trip the "site changed" warning every half hour.
        if not _is_available(module):
            log.info("%s: unavailable, skipped", name)
            continue

        # Dry runs ignore the interval: they are how you check a source by hand.
        interval = cfg.source_intervals_min.get(name, 0)
        if not args.dry_run and not is_due(store.last_run(name), interval, datetime.now(timezone.utc)):
            log.info("%s: polled under %d min ago, skipped", name, interval)
            continue

        client = clients.setdefault(
            module.TRANSPORT, HttpClient(module.TRANSPORT, cfg.curl_binary)
        )

        result: SourceResult = module.fetch(client, rotate(cfg.queries, time.time()))
        log.info("%s: %d listings%s", name, len(result.listings), f" (error: {result.error})" if result.error else "")

        if not args.dry_run:
            _check_health(store, notifier, result)

        for raw in result.listings:
            # Broad queries overlap, so the same listing arrives several times.
            key = (raw.source, raw.listing_id)
            if key in seen_this_run:
                continue

            seen_this_run.add(key)

            outcome, match = evaluate(raw, cfg.targets, cfg.rules)
            outcomes[outcome] += 1

            if match is None:
                continue

            if args.dry_run:
                print(f"  [{match.target.key}] {_describe(match, cfg.currency)}")
                continue

            candidate = _handle(match, store, mode, thresholds, args.dry_run)

            if candidate is not None:
                to_alert.append(candidate)

    # The browser transport holds a Chromium process; release it.
    for client in clients.values():
        client.close()

    alerted = _dispatch(to_alert, store, notifier)
    _report(outcomes, alerted, mode)

    return 0


def _handle(match: Match, store: Store, mode: Mode, thresholds: Dict[str, float], dry_run: bool) -> Optional[Tuple[Match, Event]]:
    """Record a match and say whether it deserves a push."""
    if dry_run:
        return None

    event = store.record(match)

    if mode is Mode.OBSERVE:
        return None

    if not _worth_pushing(match, thresholds):
        return None

    # Unchanged is only a reason to stay quiet if we already spoke up about it.
    if event is Event.UNCHANGED and store.already_notified(match):
        return None

    return match, event


def _dispatch(candidates: List[Tuple[Match, Event]], store: Store, notifier: Notifier) -> int:
    """Push the cheapest matches; summarise anything past the per-run cap."""
    ranked = sorted(candidates, key=lambda pair: pair[0].landed or 0.0)

    for match, event in ranked[:MAX_ALERTS_PER_RUN]:
        notifier.alert(match, event)
        store.mark_notified(match)

    overflow = ranked[MAX_ALERTS_PER_RUN:]

    for match, _ in overflow:
        # Marked so the backlog drains over the next runs instead of repeating.
        store.mark_notified(match)

    if overflow:
        notifier.backlog(len(overflow))

    return len(ranked)


def _worth_pushing(match: Match, thresholds: Dict[str, float]) -> bool:
    """One rule for every target; the policy only changed where the bar sits.

    VALUE targets calibrate to the 25th percentile of observed prices,
    CHEAPEST to the 10th. Both then ask the same question.
    """
    # A listing with no price ("VB", auction with no bids) is worth seeing.
    if match.landed is None:
        return True

    # Every fixed price under the plausibility floor in the first live GPU
    # run was a spare part or bait. An auction bid down there is different:
    # a real chance at a low price, and already flagged as a bid.
    floor = match.plausibility_floor
    if floor is not None and match.landed < floor and match.raw.price_kind is PriceKind.FIXED:
        return False

    return match.landed <= thresholds.get(match.target.key, match.target.fallback_max)


def _calibrate(cfg, store: Store) -> int:
    """Replace guessed price ceilings with a percentile of what we actually saw."""
    thresholds: Dict[str, float] = {}

    for target in cfg.targets:
        prices = sorted(_calibration_prices(target, store.prices_for(target.key)))
        percentile = _percentile_for(cfg, target)

        if len(prices) < cfg.min_samples:
            log.info("%-22s %2d samples — keeping fallback %s %.0f", target.key, len(prices), cfg.currency, target.fallback_max)
            continue

        cut = _percentile(prices, percentile)
        thresholds[target.key] = round(cut, 2)
        log.info("%-22s %2d samples — p%d threshold %s %.0f (median %.0f)", target.key, len(prices), percentile, cfg.currency, cut, statistics.median(prices))

    config.save_thresholds(cfg.thresholds_path, thresholds)
    log.info("wrote %s — now set mode = \"alert\" in config.toml", cfg.thresholds_path)

    return 0


def _calibration_prices(target, prices: List[float]) -> List[float]:
    """Drop prices below the plausibility floor: bait would drag the bar down."""
    if target.min_plausible is None:
        return prices

    return [p for p in prices if p >= target.min_plausible]


def _percentile_for(cfg, target) -> int:
    """A cheapest target sits at the bottom of the market, not merely under it."""
    if target.price_policy is PricePolicy.CHEAPEST:
        return cfg.cheapest_percentile

    return cfg.percentile


def _percentile(sorted_values: List[float], percentile: int) -> float:
    index = max(0, min(len(sorted_values) - 1, round(percentile / 100 * (len(sorted_values) - 1))))

    return sorted_values[index]


def _check_health(store: Store, notifier: Notifier, result: SourceResult) -> None:
    empty_runs = store.record_health(result.source, len(result.listings))

    if empty_runs == EMPTY_RUNS_BEFORE_WARNING:
        notifier.health(result.source, empty_runs)


def is_due(last_run: Optional[datetime], interval_min: int, now: datetime) -> bool:
    """Whether a source's polling interval has passed since its last poll."""
    if last_run is None or interval_min <= 0:
        return True

    return now - last_run >= timedelta(minutes=interval_min) - TIMER_JITTER_SLACK


def rotate(queries: List, now: float) -> List:
    """Same queries, different starting point per timer slot."""
    if not queries:
        return queries

    offset = int(now // ROTATION_SLOT_S) % len(queries)

    return queries[offset:] + queries[:offset]


def _is_available(module) -> bool:
    """Sources with an optional dependency expose `available()`; most do not."""
    check = getattr(module, "available", None)

    return check() if check else True


def _selected_sources(cfg, only: str) -> List[str]:
    names = [n for n in cfg.enabled_sources if n in SOURCES]

    if only:
        return [n for n in names if n == only]

    return names


def _thresholds(cfg) -> Dict[str, float]:
    thresholds = config.load_thresholds(cfg.thresholds_path)

    if cfg.mode is Mode.ALERT and not thresholds:
        log.warning("no calibrated thresholds yet — using per-target fallbacks")

    return thresholds


def _describe(match: Match, currency: str) -> str:
    price = f"{currency} {match.landed:.0f}" if match.landed is not None else "no price"
    flags = f"  [{', '.join(match.flags)}]" if match.flags else ""

    return f"{price:>10}  {match.raw.title[:70]}{flags}\n{' ' * 14}{match.raw.url}"


def _report(outcomes: Counter, alerted: int, mode: Mode) -> None:
    seen = sum(outcomes.values())
    kept = outcomes[Outcome.MATCH]
    log.info(
        "%d listings seen, %d matched, %d pushed (mode=%s)", seen, kept, alerted, mode.value
    )

    dropped = ", ".join(
        f"{outcome.value}={count}" for outcome, count in outcomes.items() if outcome is not Outcome.MATCH
    )
    if dropped:
        log.info("dropped: %s", dropped)


def _parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG,
                        help="buyer config (default: ./dealhunt.toml)")
    parser.add_argument("--dry-run", action="store_true", help="print matches, touch nothing")
    parser.add_argument("--source", default="", help="restrict to one source")
    parser.add_argument("--calibrate", action="store_true", help="recompute alert thresholds")
    parser.add_argument("--notify-test", action="store_true", help="send one test push")
    parser.add_argument("--report-failure", default="", metavar="UNIT",
                        help="push a crash alert; invoked by the OnFailure unit")
    parser.add_argument("-v", "--verbose", action="store_true")

    return parser.parse_args(argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
