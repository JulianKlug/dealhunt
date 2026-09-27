"""Store + alert-decision tests.

These cover the transitions that are easy to get wrong and impossible to notice
in production: a listing collected while observing must still fire once alerts
are armed, and it must not fire twice.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


from dealhunt import cli as run  # noqa: E402
from dealhunt.models import (  # noqa: E402
    ConditionVerdict,
    Event,
    Match,
    Mode,
    PricePolicy,
    RawListing,
    
    ShippingVerdict,
    
    SizeVerdict,
    Target,
)
from dealhunt.store.db import Store  # noqa: E402

TARGET = Target(
    key="deeluxe-spark-xv",
    label="Deeluxe Spark XV",
    patterns=["spark ?xv"],
    context=["boot"],
    exclude=[],
    size_scheme="shoe",
    price_policy=PricePolicy.VALUE,
    fallback_max=260.0,
)

THRESHOLDS = {TARGET.key: 250.0}


class FakeNotifier:
    def __init__(self):
        self.alerts = []
        self.backlogs = []

    def alert(self, match, event):
        self.alerts.append((match.key, event))

    def backlog(self, count):
        self.backlogs.append(count)


def make_match(listing_id="1", price=200.0):
    raw = RawListing(
        source="test",
        listing_id=listing_id,
        title="Deeluxe Spark XV Boots Gr. 43",
        url="https://example.test/" + listing_id,
        country="CH",
        price=price,
    )

    return Match(
        raw=raw,
        target=TARGET,
        size=SizeVerdict.ACCEPTED,
        condition=ConditionVerdict.OK,
        shipping=ShippingVerdict.DELIVERABLE,
        landed=price,
    )


@pytest.fixture
def store(tmp_path):
    db = Store(tmp_path / "test.sqlite")
    yield db
    db.close()


def handle(match, store, mode=Mode.ALERT):
    return run._handle(match, store, mode, THRESHOLDS, dry_run=False)


def test_new_cheap_listing_alerts(store):
    assert handle(make_match(), store) is not None


def test_the_same_listing_does_not_alert_twice(store):
    first = handle(make_match(), store)
    run._dispatch([first], store, FakeNotifier())

    assert handle(make_match(), store) is None


def test_listing_collected_while_observing_alerts_once_armed(store):
    """The observe -> alert switch must not swallow the whole backlog."""
    handle(make_match(), store, mode=Mode.OBSERVE)

    assert handle(make_match(), store, mode=Mode.ALERT) is not None


def test_price_drop_alerts_again(store):
    first = handle(make_match(price=200.0), store)
    run._dispatch([first], store, FakeNotifier())

    assert handle(make_match(price=150.0), store) is not None


def test_expensive_listing_stays_quiet(store):
    assert handle(make_match(price=400.0), store) is None


def test_listing_without_a_price_is_still_worth_seeing(store):
    match = make_match()
    match.landed = None

    assert handle(match, store) is not None


def test_dispatch_caps_the_push_list_and_summarises_the_rest(store):
    notifier = FakeNotifier()
    candidates = [
        (make_match(listing_id=str(i), price=float(i)), Event.NEW)
        for i in range(run.MAX_ALERTS_PER_RUN + 5)
    ]

    run._dispatch(candidates, store, notifier)

    assert len(notifier.alerts) == run.MAX_ALERTS_PER_RUN
    assert notifier.backlogs == [5]
    # Cheapest first, and the overflow is marked so it never repeats.
    assert notifier.alerts[0][0] == "test:0"


def test_source_health_counts_consecutive_empty_polls(store):
    assert store.record_health("tutti", 0) == 1
    assert store.record_health("tutti", 0) == 2
    assert store.record_health("tutti", 7) == 0


# --- cheapest-deal policy --------------------------------------------------

CHEAPEST_TARGET = Target(
    key="touring-ski-boots",
    label="Backcountry ski boots",
    patterns=["skitouren"],
    context=["schuh"],
    exclude=["steigeisen"],
    size_scheme="shoe",
    price_policy=PricePolicy.CHEAPEST,
    fallback_max=400.0,
)

# What calibration produced for this target: the 10th percentile, not the 25th.
CHEAPEST_THRESHOLDS = {CHEAPEST_TARGET.key: 62.0}


def cheapest_match(listing_id="1", price=300.0):
    match = make_match(listing_id=listing_id, price=price)
    match.target = CHEAPEST_TARGET
    match.landed = price

    return match


def handle_cheapest(match, store):
    return run._handle(match, store, Mode.ALERT, CHEAPEST_THRESHOLDS, dry_run=False)


def test_a_listing_at_the_bottom_decile_alerts(store):
    assert handle_cheapest(cheapest_match(price=62.0), store) is not None


def test_a_listing_above_the_bottom_decile_stays_quiet(store):
    """A CHF 80 boot may be a fine deal, but this target is asked for the floor."""
    assert handle_cheapest(cheapest_match(price=80.0), store) is None


def test_one_outlier_cannot_lock_the_target_shut(store):
    """The failure that killed the record-low rule: a CHF 40 listing must not
    silence everything above CHF 44 for the rest of the season."""
    handle_cheapest(cheapest_match(listing_id="outlier", price=40.0), store)

    assert handle_cheapest(cheapest_match(listing_id="good", price=62.0), store) is not None


def test_before_calibration_the_fallback_ceiling_applies(store):
    assert run._handle(cheapest_match(price=380.0), store, Mode.ALERT, {}, dry_run=False) is not None
    assert run._handle(cheapest_match(price=500.0), store, Mode.ALERT, {}, dry_run=False) is None


def test_cheapest_targets_calibrate_to_a_lower_percentile():
    class Cfg:
        percentile = 25
        cheapest_percentile = 10

    assert run._percentile_for(Cfg, CHEAPEST_TARGET) == 10
    assert run._percentile_for(Cfg, TARGET) == 25


# --- per-source polling interval -------------------------------------------

from datetime import datetime, timedelta, timezone  # noqa: E402

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
RICARDO_INTERVAL_MIN = 120


def test_a_never_polled_source_is_due():
    assert run.is_due(None, RICARDO_INTERVAL_MIN, NOW)


def test_a_source_polled_recently_is_skipped():
    assert not run.is_due(NOW - timedelta(minutes=90), RICARDO_INTERVAL_MIN, NOW)


def test_a_source_is_due_once_its_interval_has_passed():
    assert run.is_due(NOW - timedelta(minutes=120), RICARDO_INTERVAL_MIN, NOW)


def test_timer_jitter_does_not_skip_a_whole_extra_slot():
    """The timer fires every 30 min +- 5 min. A poll that lands 116 minutes
    after the last one must count, or ricardo slips to every 2.5 hours."""
    assert run.is_due(NOW - timedelta(minutes=116), RICARDO_INTERVAL_MIN, NOW)


def test_a_source_without_an_interval_runs_every_time():
    assert run.is_due(NOW - timedelta(minutes=1), 0, NOW)


def test_store_remembers_when_a_source_last_ran(store):
    assert store.last_run("ricardo") is None

    store.record_health("ricardo", 10)

    assert store.last_run("ricardo") is not None


# --- price drops compare the seller's price, not our cost estimate ---------

def test_a_change_in_our_cost_model_is_not_a_price_drop(store):
    """Dropping the courier fee cut every German landed price by ~CHF 16 and
    re-pushed seven listings whose sellers had changed nothing."""
    before = make_match(price=200.0)
    store.record(before)

    after = make_match(price=200.0)
    after.landed = 170.0          # same sticker, cheaper route

    assert store.record(after) is Event.UNCHANGED


def test_a_real_sticker_cut_is_still_a_price_drop(store):
    store.record(make_match(price=200.0))

    assert store.record(make_match(price=150.0)) is Event.PRICE_DROP
