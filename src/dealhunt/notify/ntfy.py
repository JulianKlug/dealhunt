"""Push notifications via ntfy.sh.

The only outbound network call in the project. A good listing on kleinanzeigen
is gone in hours, so the alert has to reach a phone, not a log file.

Pick an unguessable topic name — anyone who knows it can read your alerts.
"""

from __future__ import annotations

import logging
from typing import Optional

import requests

from dealhunt.models import Event, Match

log = logging.getLogger(__name__)

NTFY_BASE = "https://ntfy.sh"
TIMEOUT_S = 15

# ntfy priorities: 3 is default, 4 shows as high, 5 buzzes through Do Not Disturb.
PRIORITY_NORMAL = 4
PRIORITY_HEALTH = 3
PRIORITY_URGENT = 5


class Notifier:
    def __init__(self, topic: str, currency: str, enabled: bool = True):
        self._url = f"{NTFY_BASE}/{topic}"
        self._currency = currency
        self._enabled = enabled

    def alert(self, match: Match, event: Event) -> None:
        title = f"{_prefix(event)} {match.target.label} — {_price(match, self._currency)}"
        self._post(title, _body(match), PRIORITY_NORMAL, match.raw.url, tags="moneybag")

    def health(self, source: str, empty_runs: int) -> None:
        self._post(
            f"dealhunt: {source} returned nothing {empty_runs}x",
            "The site layout or its bot protection probably changed. "
            "Run with --source " + source + " --dry-run to see what it returns.",
            PRIORITY_HEALTH,
            click=None,
            tags="warning",
        )

    def backlog(self, count: int) -> None:
        self._post(
            f"dealhunt: {count} more matches under threshold",
            "Capped to keep the push list readable. Query data/dealhunt.sqlite "
            "for the full set, or tighten the thresholds.",
            PRIORITY_HEALTH,
            click=None,
            tags="mag",
        )

    def failure(self, unit: str) -> None:
        """The job died before it could reach a source.

        Nothing else catches this: a crashing service pushes no alerts and
        trips no source-health warning, so it looks exactly like a quiet
        market. It stays silent until you go looking, which is too late.
        """
        self._post(
            f"dealhunt CRASHED: {unit}",
            "The poll is not running. Diagnose with:\n"
            "journalctl --user -u dealhunt.service -n 40",
            PRIORITY_URGENT,
            click=None,
            tags="rotating_light",
        )

    def test(self) -> None:
        self._post("dealhunt is wired up", "If you can read this, alerts work.", PRIORITY_HEALTH, None, "white_check_mark")

    def _post(self, title: str, body: str, priority: int, click: Optional[str], tags: str = "") -> None:
        if not self._enabled:
            log.info("[dry-run] %s | %s", title, body.replace("\n", " ")[:160])
            return

        headers = {"Title": title.encode("utf-8"), "Priority": str(priority)}

        if click:
            headers["Click"] = click
        if tags:
            headers["Tags"] = tags

        try:
            requests.post(self._url, data=body.encode("utf-8"), headers=headers, timeout=TIMEOUT_S)
        except requests.RequestException as exc:
            log.error("ntfy push failed: %s", exc)


def _prefix(event: Event) -> str:
    return "PRICE DROP:" if event is Event.PRICE_DROP else "New:"


def _price(match: Match, currency: str) -> str:
    if match.landed is None:
        return "price on request"

    landed = f"{currency} {match.landed:.0f}"
    sticker = match.raw.price

    # When import or exchange moves the price, the landed figure is the honest
    # one, but the seller's own number is what you will see on the listing.
    if sticker is None or (match.raw.currency.upper() == currency and abs(sticker - match.landed) < 0.5):
        return landed

    return f"{landed} landed ({sticker:.0f} {match.raw.currency})"


def _body(match: Match) -> str:
    lines = [match.raw.title.strip()]

    where = ", ".join(part for part in (match.raw.source, match.raw.location) if part)
    lines.append(where)

    if match.flags:
        lines.append("⚠ " + " · ".join(match.flags))

    lines.append(match.raw.url)

    return "\n".join(lines)
