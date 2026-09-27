"""Shared data contracts.

Every layer speaks these types and nothing else: a source turns a marketplace
into RawListing, the matcher turns RawListing into Match, the store and the
notifier consume Match. No layer ever sees another layer's internals.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import re
from typing import Dict, FrozenSet, List, Optional, Tuple


class Mode(Enum):
    """OBSERVE logs matches to build a price distribution; ALERT pushes them."""

    OBSERVE = "observe"
    ALERT = "alert"


class PriceKind(Enum):
    """Whether the listed price is what the item costs.

    An auction shows its current bid, which is a floor, not a price: a CHF 50
    bid on a 3090 with three days to run says nothing about what it will fetch.
    """

    FIXED = "fixed"
    AUCTION = "auction"


class PricePolicy(Enum):
    """How a target's asking price is judged.

    VALUE wants a good boot at a fair price: anything at or under the
    calibrated bar is worth a look. CHEAPEST wants the floor of the market:
    only listings at or near the lowest price ever seen for that target.
    """

    VALUE = "value"
    CHEAPEST = "cheapest"


class TextScope(Enum):
    """Which listing text a target's patterns read.

    FULL suits boots, whose size and model often hide in the body. TITLE suits
    GPUs, whose bodies casually mention other cards ("faster than my old 3090")
    and other hardware ("was in my PC"), which would fire patterns and excludes.
    """

    FULL = "full"
    TITLE = "title"


class SizeVerdict(Enum):
    ACCEPTED = "accepted"   # an accepted size token is present
    ABSENT = "absent"       # no size stated anywhere — worth a look, flagged
    CONFLICT = "conflict"   # a different size is explicitly stated — drop it


class ConditionVerdict(Enum):
    OK = "ok"
    HEAVILY_USED = "heavily_used"
    BROKEN = "broken"


class ShippingVerdict(Enum):
    """Whether the item can reach one of the buyer's delivery addresses."""

    DELIVERABLE = "deliverable"
    UNSTATED = "unstated"


class Outcome(Enum):
    """Why the matcher kept or dropped a listing. Everything but MATCH is a drop."""

    MATCH = "match"
    NO_TARGET = "no_target"
    WANTED_AD = "wanted_ad"
    ACCESSORY = "accessory"
    WRONG_SIZE = "wrong_size"
    BROKEN = "broken"


class Event(Enum):
    """What the store saw when it wrote a match — drives whether we push."""

    NEW = "new"
    PRICE_DROP = "price_drop"
    UNCHANGED = "unchanged"


@dataclass(frozen=True)
class SizeSpec:
    """The sizes a buyer takes, in one sizing scheme.

    Personal, so it lives in the buyer's config, never in a shared target file:
    a target only says which scheme it is sized in ("shoe").
    """

    accept: FrozenSet[str]                       # normalised: "43", "27.5"
    accept_alt: FrozenSet[str] = frozenset()     # a second scale, e.g. US shoe


@dataclass(frozen=True)
class Target:
    """One thing being hunted, e.g. "Deeluxe Spark XV" or "any 24 GB GPU".

    Prices are in the buyer's home currency.
    """

    key: str
    label: str
    patterns: List[str]          # regexes, any one hit identifies the item
    context: List[str]           # regexes, one must also hit: what kind of thing
                                 # it is, so a board named like a boot is not one
    exclude: List[str]           # regexes, any hit disqualifies: neighbouring
                                 # items that mention the same words
    price_policy: PricePolicy
    fallback_max: float          # alert bar until calibration has data
    # None: the item has no size. Otherwise the name of a sizing scheme the
    # buyer's config gives accepted sizes for, e.g. "shoe".
    size_scheme: Optional[str] = None
    # Spare parts sold under the item's name ("Ersatzglas", "lens only"). Read
    # from the title, and forgiven when the title says the item comes *with*
    # the part ("inkl. Ersatzglas").
    accessories: Tuple[str, ...] = ()
    # Regexes that disqualify on the title alone, whatever the match scope:
    # "junior", "kids" — sizes of a thing, not the thing.
    title_exclude: Tuple[str, ...] = ()
    # Below this, a listing is flagged and kept out of calibration: on GPUs a
    # far-too-cheap price is usually a scam, and it would drag the bar down.
    min_plausible: Optional[float] = None
    # Stricter floors for pricier models within the target, by title regex:
    # {"4090": 1000} — a "new 4090" at a 3090's price is not a bargain.
    plausible_floors: Optional[Dict[str, float]] = None
    # Search terms that find this target; merged across targets and deduped.
    queries: Tuple[str, ...] = ()
    match_scope: Optional["TextScope"] = None  # FULL when omitted

    def __post_init__(self):
        if self.match_scope is None:
            object.__setattr__(self, "match_scope", TextScope.FULL)


@dataclass(frozen=True)
class SearchQuery:
    """A search term to run against every enabled source."""

    text: str
    pages: int = 1


@dataclass
class RawListing:
    """A marketplace listing, normalised but not yet judged."""

    source: str
    listing_id: str
    title: str
    url: str
    country: str                  # ISO code of where it ships from: "CH", "DE"
    description: str = ""
    price: Optional[float] = None
    price_kind: PriceKind = PriceKind.FIXED
    currency: str = "CHF"
    shipping_cost: Optional[float] = None
    location: str = ""

    @property
    def haystack(self) -> str:
        """Everything the matcher is allowed to read."""
        return f"{self.title}\n{self.description}"


@dataclass
class Match:
    """A listing the matcher kept, with its verdicts and true landed cost."""

    raw: RawListing
    target: Target
    size: SizeVerdict
    condition: ConditionVerdict
    shipping: ShippingVerdict
    landed: Optional[float]       # in the buyer's home currency
    # Where the buyer can receive, for the "ask about shipping" flag: "FR/CH".
    addresses: str = ""

    @property
    def key(self) -> str:
        return f"{self.raw.source}:{self.raw.listing_id}"

    @property
    def plausibility_floor(self) -> Optional[float]:
        """The lowest believable price for what this title says it is."""
        floors = [self.target.min_plausible] if self.target.min_plausible else []

        for pattern, floor in (self.target.plausible_floors or {}).items():
            if re.search(pattern, self.raw.title, re.I):
                floors.append(floor)

        return max(floors) if floors else None

    @property
    def _implausibly_cheap(self) -> bool:
        floor = self.plausibility_floor

        return floor is not None and self.landed is not None and self.landed < floor

    @property
    def flags(self) -> List[str]:
        """Short human-readable caveats, shown in the push notification."""
        out = []

        if self.size is SizeVerdict.ABSENT:
            out.append("size unstated")
        if self.condition is ConditionVerdict.HEAVILY_USED:
            out.append("heavily used")
        if self.shipping is ShippingVerdict.UNSTATED:
            out.append(f"ask about shipping to {self.addresses}")
        if self.raw.price_kind is PriceKind.AUCTION:
            out.append("auction — current bid, final price will be higher")
        if self._implausibly_cheap:
            out.append("implausibly cheap — verify the seller before paying")

        return out


@dataclass
class SourceResult:
    """What one source returned in one poll — also the health signal."""

    source: str
    listings: List[RawListing] = field(default_factory=list)
    error: Optional[str] = None
    blocked: bool = False       # the site refused us; stop asking this run
