"""Pure matching logic: RawListing in, verdict out.

No I/O, no config parsing, no network: every function here is deterministic
and unit-testable, which is what lets the runtime work without a model.

What is generic lives here: the vocabulary of condition, wanted ads and
bundles in German, French and English. What belongs to one kind of item
(model names, spare parts, sizes) lives in its target file and in sizes.py.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Dict, FrozenSet, List, Optional, Sequence, Tuple

from dealhunt.matching.sizes import SCHEMES
from dealhunt.models import (
    ConditionVerdict,
    Match,
    Outcome,
    RawListing,
    ShippingVerdict,
    SizeSpec,
    SizeVerdict,
    Target,
    TextScope,
)

# --- the generic language layer ---------------------------------------------

_BROKEN_RE = re.compile(
    r"defekt|kaputt|riss(?:ig)?\b|bastler|ersatzteil|for parts|broken|not working"
    r"|nur f(?:ü|ue)r teile|besch(?:ä|ae)digt"
    r"|schnalle fehlt|fehlt eine schnalle|ohne schnalle|riss in der schale"
    r"|pour pi(?:è|e)ces|ne fonctionne pas|\bhs\b|cass(?:é|e)e?\b|d(?:é|e)fectueu",
    re.I,
)
_HEAVY_USE_RE = re.compile(
    r"viel benutzt|viel gefahren|viel gebraucht|stark gebraucht|abgenutzt|durchgelaufen"
    r"|heavily used|well used|lots of use"
    r"|satisfaisant|tr(?:è|e)s us(?:é|e)|bien us(?:é|e)",
    re.I,
)

# "Ships abroad" in general. Phrases naming a specific destination ("Versand
# in die Schweiz") are the buyer's, and come from their config.
_SHIPS_ABROAD_RE = re.compile(
    r"eu-?weit|europaweit|ins (?:eu-)?ausland|international(?:er)? versand"
    r"|weltweiter versand|worldwide shipping|ships? internationally",
    re.I,
)

# A title naming a spare part is the part, unless it says the item comes with
# it: "Ersatzglas" is a lens, "Anon M4 inkl. Ersatzglas" is goggles plus a lens.
_INCLUDES_RE = re.compile(r"\b(?:inkl\.?|inklusive|incl\.?|including|mit|with|\+)\b|\+", re.I)

# Wanted ads sit in the same result set and look identical to a keyword matcher.
_WANTED_AD_RE = re.compile(
    r"^\s*(?:kaufe|wanted|looking for|tausche)\b"
    # "suche" and "gesucht" anywhere: kleinanzeigen titles often lead with a
    # place or a date ("70825 Korntal … suche RTX 4090"), or end with it
    # ("RTX 3090 gesucht").
    r"|\bsuche\b|\bgesucht\b|\bcherche\b|\brecherche\b|\[ach\]"
    # Dealers buying: "Wir kaufen …", "Ankauf RTX 5090".
    r"|\bwir kaufen\b|\bankauf\b",
    re.I,
)


@dataclass(frozen=True)
class Delivery:
    """Where the buyer can receive goods, and what bringing them home costs.

    Example, a buyer in Switzerland who can also receive in France:
        home_country="CH", addresses={"CH", "FR"}, import_vat_rate=0.081,
        import_allowance=150 — goods from abroad are carried across the
        border, and VAT is due on the whole value once over the allowance.
    """

    home_country: str
    addresses: FrozenSet[str]
    import_vat_rate: float = 0.0
    import_allowance: float = 0.0
    # Regexes meaning "ships to one of my addresses", in sellers' languages.
    ships_to: Tuple[str, ...] = ()

    @property
    def label(self) -> str:
        """"FR/CH" — the addresses, as a seller-facing question."""
        return "/".join(sorted(self.addresses))


@dataclass(frozen=True)
class RuleSet:
    """Everything the matcher needs that is not a Target: the buyer."""

    delivery: Delivery
    fx: Dict[str, float]                          # to home currency: {"EUR": 0.94}
    sizes: Dict[str, SizeSpec] = field(default_factory=dict)   # scheme -> spec


def evaluate(
    raw: RawListing, targets: Sequence[Target], rules: RuleSet, trace: Optional[List[str]] = None
) -> Tuple[Outcome, Optional[Match]]:
    """Judge one listing. Returns why it was dropped, or the Match it became.

    Pass a list as `trace` to have every decision appended to it, in order;
    `dealhunt --explain` prints it. Tracing never changes the verdict.
    """
    note = trace.append if trace is not None else _discard
    text = raw.haystack

    wanted = _WANTED_AD_RE.search(raw.title)
    if wanted:
        note(f"WANTED_AD — the title says {wanted.group(0)!r}")
        return Outcome.WANTED_AD, None

    target, dropped_as = find_target(raw, targets, note)
    if target is None:
        note(f"{dropped_as.name} — no target accepted it")
        return dropped_as, None

    condition = condition_of(text)
    if condition is ConditionVerdict.BROKEN:
        note(f"BROKEN — the text says {_BROKEN_RE.search(text).group(0)!r}")
        return Outcome.BROKEN, None

    size = _size_for(target, text, rules)
    note(f"size: {size.value}" + (f" (scheme {target.size_scheme!r})" if target.size_scheme else " (unsized item)"))
    if size is SizeVerdict.CONFLICT:
        note("WRONG_SIZE — a size is stated, and it is not one you take")
        return Outcome.WRONG_SIZE, None

    match = Match(
        raw=raw,
        target=target,
        size=size,
        condition=condition,
        shipping=shipping_verdict(raw, text, rules.delivery),
        landed=landed(raw, rules),
        addresses=rules.delivery.label,
    )

    note(f"MATCH {target.key} — landed {match.landed}, flags {match.flags}")
    return Outcome.MATCH, match


def find_target(
    raw: RawListing, targets: Sequence[Target], note: Callable[[str], None] = None
) -> Tuple[Optional[Target], Outcome]:
    """First target the listing is genuinely an instance of.

    Returns the reason for the drop when there is none: a spare part named
    after a target reports ACCESSORY, anything else NO_TARGET.
    """
    note = note or _discard
    dropped_as = Outcome.NO_TARGET

    for target in targets:
        text = raw.title if target.match_scope is TextScope.TITLE else raw.haystack

        pattern = _first_match(target.patterns, text)
        if pattern is None:
            continue

        note(f"{target.key}: pattern '{pattern}' hit ({target.match_scope.value} text)")

        if target.context and _first_match(target.context, text) is None:
            note(f"{target.key}: rejected — no context word ({', '.join(target.context)})")
            continue

        excluded = _first_match(target.exclude, text)
        if excluded:
            note(f"{target.key}: rejected — exclude '{excluded}' hit")
            continue

        title_excluded = _first_match(target.title_exclude, raw.title)
        if title_excluded:
            note(f"{target.key}: rejected — title_exclude '{title_excluded}' hit")
            dropped_as = Outcome.ACCESSORY
            continue

        if is_accessory(raw.title, target.accessories):
            note(f"{target.key}: rejected — the title names a spare part, with no 'inkl./mit/+'")
            dropped_as = Outcome.ACCESSORY
            continue

        note(f"{target.key}: accepted")
        return target, Outcome.MATCH

    return None, dropped_as


def is_accessory(title: str, terms: Sequence[str]) -> bool:
    """A spare part sold under the item's name, rather than the item."""
    if not terms or not _any_match(terms, title):
        return False

    return not _INCLUDES_RE.search(title)


def size_verdict(text: str, scheme: str, spec: SizeSpec) -> SizeVerdict:
    main, alt = SCHEMES[scheme](text)

    if main & spec.accept or alt & spec.accept_alt:
        return SizeVerdict.ACCEPTED

    if main or alt:
        return SizeVerdict.CONFLICT

    return SizeVerdict.ABSENT


def condition_of(text: str) -> ConditionVerdict:
    if _BROKEN_RE.search(text):
        return ConditionVerdict.BROKEN

    if _HEAVY_USE_RE.search(text):
        return ConditionVerdict.HEAVILY_USED

    return ConditionVerdict.OK


def shipping_verdict(raw: RawListing, text: str, delivery: Delivery) -> ShippingVerdict:
    # A seller in a country the buyer can receive in ships domestically.
    if raw.country in delivery.addresses:
        return ShippingVerdict.DELIVERABLE

    if _SHIPS_ABROAD_RE.search(text) or _any_match(delivery.ships_to, text):
        return ShippingVerdict.DELIVERABLE

    return ShippingVerdict.UNSTATED


def landed(raw: RawListing, rules: RuleSet) -> Optional[float]:
    """What it costs to have the item in hand at home, in home currency.

    Goods from abroad are carried across the border: import VAT on the whole
    value once it exceeds the allowance, and nothing when under it.
    """
    if raw.price is None:
        return None

    rate = rules.fx.get(raw.currency.upper(), 1.0)
    total = (raw.price + (raw.shipping_cost or 0.0)) * rate
    delivery = rules.delivery

    if raw.country == delivery.home_country or total <= delivery.import_allowance:
        return round(total, 2)

    return round(total * (1 + delivery.import_vat_rate), 2)


def _size_for(target: Target, text: str, rules: RuleSet) -> SizeVerdict:
    if target.size_scheme is None:
        return SizeVerdict.ACCEPTED

    return size_verdict(text, target.size_scheme, rules.sizes[target.size_scheme])


def _any_match(patterns: Sequence[str], text: str) -> bool:
    return _first_match(patterns, text) is not None


def _first_match(patterns: Sequence[str], text: str) -> Optional[str]:
    """The first pattern that hits, so a trace can say which one."""
    return next((p for p in patterns if re.search(p, text, re.I)), None)


def _discard(_: str) -> None:
    """A trace that goes nowhere: the default, and free."""
