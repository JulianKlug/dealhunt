"""Deterministic maintenance tools: poll log, report, explain.

These are the facts a maintainer (a person, or an LLM through a skill) looks
at. None of them decides anything: they read the store and the matcher.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from conftest import EXAMPLE, RULES
from dealhunt import report
from dealhunt.matching.rules import evaluate
from dealhunt.models import PollState, RawListing
from dealhunt.store.db import Store


@pytest.fixture
def store(tmp_path):
    db = Store(tmp_path / "t.sqlite")
    yield db
    db.close()


def listing(title, description="", price=150.0, country="CH"):
    return RawListing(source="t", listing_id="1", title=title, url="https://x.test",
                      country=country, description=description, price=price)


# --- poll log --------------------------------------------------------------

def test_every_poll_is_logged_not_just_the_last(store):
    for count in (10, 12, 0):
        store.record_health("tutti", count)

    assert [p.count for p in store.poll_history(days=1)] == [10, 12, 0]


def test_a_block_is_logged_as_such(store):
    store.record_health("ricardo", 0, PollState.BLOCKED, error="HTTP 403")
    poll = store.poll_history(days=1)[0]

    assert poll.blocked and poll.error == "HTTP 403"


# --- report ----------------------------------------------------------------

def test_report_flags_a_source_that_keeps_returning_the_same_count(store):
    """The kleinanzeigen bug: HTTP 200, a plausible count, the wrong ads, 11 days."""
    for _ in range(report.CONSTANT_RUNS):
        store.record_health("kleinanzeigen", 112)

    text = report.render(store, EXAMPLE, days=7)

    assert "kleinanzeigen" in text and "same count" in text


def test_report_flags_blocked_polls(store):
    store.record_health("ricardo", 0, PollState.BLOCKED, error="HTTP 403")
    store.record_health("ricardo", 650)

    text = report.render(store, EXAMPLE, days=7)

    assert "ricardo" in text and "blocked 1" in text


def test_report_has_every_section(store):
    text = report.render(store, EXAMPLE, days=7)

    for heading in ("## Sources", "## Targets", "## Recent pushes", "## Cheapest matches"):
        assert heading in text


# --- explain ---------------------------------------------------------------

def test_explain_names_the_pattern_that_matched():
    trace = []
    evaluate(listing("Deeluxe Spark XV Boots Gr. 43"), EXAMPLE.targets, RULES, trace=trace)

    assert any("deeluxe-spark-xv" in line and "pattern" in line for line in trace)
    assert trace[-1].startswith("MATCH")


def test_explain_names_the_exclude_that_dropped_it():
    trace = []
    evaluate(listing("Gaming Laptop RTX 4090 i9", price=1500.0), EXAMPLE.targets, RULES, trace=trace)

    assert any("gpu-24gb" in line and "exclude" in line and "laptop" in line for line in trace)


def test_explain_reports_a_size_conflict():
    trace = []
    evaluate(listing("Deeluxe Spark XV Boots Gr. 41"), EXAMPLE.targets, RULES, trace=trace)

    assert trace[-1].startswith("WRONG_SIZE")


def test_tracing_does_not_change_the_verdict():
    raw = listing("Deeluxe Spark XV Boots Gr. 43")

    assert evaluate(raw, EXAMPLE.targets, RULES)[0] is evaluate(raw, EXAMPLE.targets, RULES, trace=[])[0]
