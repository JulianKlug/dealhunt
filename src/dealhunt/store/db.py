"""SQLite persistence: what we have already seen, and what it cost.

Two jobs:
  1. Suppress duplicate alerts across runs (a listing sits up for weeks).
  2. Accumulate the price distribution that calibration turns into thresholds,
     so the alert bar comes from the real market rather than a guess.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from dealhunt.models import Event, Match

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    key         TEXT PRIMARY KEY,      -- "<source>:<listing_id>"
    source      TEXT NOT NULL,
    target_key  TEXT NOT NULL,
    title       TEXT NOT NULL,
    url         TEXT NOT NULL,
    landed      REAL,                  -- in the buyer's home currency
    size        TEXT NOT NULL,
    condition   TEXT NOT NULL,
    price_kind  TEXT NOT NULL DEFAULT 'fixed',
    price       REAL,                  -- the seller's own price, in their currency
    first_seen  TEXT NOT NULL,
    last_seen   TEXT NOT NULL,
    notified_at TEXT
);
CREATE INDEX IF NOT EXISTS listings_by_target ON listings (target_key, landed);

CREATE TABLE IF NOT EXISTS source_health (
    source     TEXT PRIMARY KEY,
    last_run   TEXT NOT NULL,
    last_count INTEGER NOT NULL,
    empty_runs INTEGER NOT NULL DEFAULT 0
);
"""

# A drop smaller than this, in the seller's own currency, is noise, not a deal.
MIN_MEANINGFUL_DROP = 5.0


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path))
        self._db.row_factory = sqlite3.Row
        # Migrate first: the schema script indexes columns an old database
        # only has under their old names.
        self._migrate()
        self._db.executescript(SCHEMA)

    def _migrate(self) -> None:
        """Bring a database created by an older version up to the schema."""
        columns = {row["name"] for row in self._db.execute("PRAGMA table_info(listings)")}

        if not columns:
            return  # a fresh database: the schema script creates everything

        # Before dealhunt went generic, landed cost was always in CHF.
        if "landed_chf" in columns:
            self._db.execute("ALTER TABLE listings RENAME COLUMN landed_chf TO landed")

        if "price_kind" not in columns:
            self._db.execute(
                "ALTER TABLE listings ADD COLUMN price_kind TEXT NOT NULL DEFAULT 'fixed'"
            )

        if "price" not in columns:
            self._db.execute("ALTER TABLE listings ADD COLUMN price REAL")

        self._db.commit()

    def close(self) -> None:
        self._db.close()

    def record(self, match: Match) -> Event:
        """Insert or update a match; the return value says whether to shout."""
        now = _now()
        row = self._db.execute(
            "SELECT price FROM listings WHERE key = ?", (match.key,)
        ).fetchone()

        if row is None:
            self._insert(match, now)
            return Event.NEW

        self._db.execute(
            "UPDATE listings SET last_seen = ?, landed = ?, price = ? WHERE key = ?",
            (now, match.landed, match.raw.price, match.key),
        )
        self._db.commit()

        # Compare what the seller asks, not our landed estimate: a change to
        # the cost model moves every landed price without anyone cutting one.
        return _price_event(row["price"], match.raw.price)

    def already_notified(self, match: Match) -> bool:
        """Whether we have ever pushed this listing.

        Matters at the observe -> alert switch: everything collected while
        observing is "unchanged" by then, and would otherwise stay silent
        forever despite being exactly what we were looking for.
        """
        row = self._db.execute(
            "SELECT notified_at FROM listings WHERE key = ?", (match.key,)
        ).fetchone()

        return bool(row and row["notified_at"])

    def mark_notified(self, match: Match) -> None:
        self._db.execute(
            "UPDATE listings SET notified_at = ? WHERE key = ?", (_now(), match.key)
        )
        self._db.commit()

    def prices_for(self, target_key: str) -> List[float]:
        """Every landed price observed for a target — calibration's input."""
        rows = self._db.execute(
            "SELECT landed FROM listings WHERE target_key = ? AND landed IS NOT NULL "
            "AND price_kind = 'fixed'",
            (target_key,),
        ).fetchall()

        return [row["landed"] for row in rows]

    def record_health(self, source: str, count: int) -> int:
        """Track consecutive empty polls; a source that dies goes quiet, not loud."""
        row = self._db.execute(
            "SELECT empty_runs FROM source_health WHERE source = ?", (source,)
        ).fetchone()

        previous = row["empty_runs"] if row else 0
        empty_runs = previous + 1 if count == 0 else 0

        self._db.execute(
            "INSERT INTO source_health (source, last_run, last_count, empty_runs) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(source) DO UPDATE SET "
            "last_run = excluded.last_run, last_count = excluded.last_count, "
            "empty_runs = excluded.empty_runs",
            (source, _now(), count, empty_runs),
        )
        self._db.commit()

        return empty_runs

    def last_run(self, source: str) -> Optional[datetime]:
        """When a source was last actually polled, or None if never."""
        row = self._db.execute(
            "SELECT last_run FROM source_health WHERE source = ?", (source,)
        ).fetchone()

        return datetime.fromisoformat(row["last_run"]) if row else None

    def _insert(self, match: Match, now: str) -> None:
        self._db.execute(
            "INSERT INTO listings (key, source, target_key, title, url, landed, "
            "size, condition, price_kind, price, first_seen, last_seen) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                match.key,
                match.raw.source,
                match.target.key,
                match.raw.title,
                match.raw.url,
                match.landed,
                match.size.value,
                match.condition.value,
                match.raw.price_kind.value,
                match.raw.price,
                now,
                now,
            ),
        )
        self._db.commit()


def _price_event(before: Optional[float], after: Optional[float]) -> Event:
    if before is None or after is None:
        return Event.UNCHANGED

    if before - after >= MIN_MEANINGFUL_DROP:
        return Event.PRICE_DROP

    return Event.UNCHANGED


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
