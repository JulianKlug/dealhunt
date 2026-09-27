"""Unit tests for the matcher — the one part that decides what reaches your phone.

Fixtures are real listing titles observed on tutti, ricardo and kleinanzeigen
during development, so a regression here is a regression against the real market.
"""

from __future__ import annotations

import pytest


from conftest import DELIVERY, EXAMPLE, RULES, SHOE
from dealhunt.matching.rules import (
    condition_of,
    evaluate,
    is_accessory,
    landed,
    shipping_verdict,
    size_verdict,
)
from dealhunt.matching.sizes import normalise_size
from dealhunt.models import (  # noqa: E402
    ConditionVerdict,
    Outcome,
    PricePolicy,
    RawListing,
    ShippingVerdict,
    SizeVerdict,
    Target,
)

BOOTS = Target(
    key="deeluxe-spark-xv",
    label="Deeluxe Spark XV",
    patterns=["deeluxe.{0,15}spark", "spark ?xv"],
    context=["boot", "schuh", "stiefel"],
    exclude=[],
    size_scheme="shoe",
    price_policy=PricePolicy.VALUE,
    fallback_max=260.0,
)

GOGGLES = Target(
    key="anon-m4",
    label="Anon M4",
    patterns=["anon.{0,8}m4"],
    context=["brille", "goggle", "maske"],
    exclude=[],
    size_scheme=None,
    price_policy=PricePolicy.VALUE,
    fallback_max=90.0,
)

TARGETS = [BOOTS, GOGGLES]


def listing(title, description="", price=200.0, currency="CHF", country="CH", shipping=None):
    return RawListing(
        source="test",
        listing_id="1",
        title=title,
        description=description,
        price=price,
        currency=currency,
        shipping_cost=shipping,
        url="https://example.test/1",
        country=country,
    )


# --- size ------------------------------------------------------------------

@pytest.mark.parametrize(
    "text, expected",
    [
        ("Deeluxe Spark XV Boots Gr. 43", SizeVerdict.ACCEPTED),
        ("Spark XV Schuhe Grösse 27,5", SizeVerdict.ACCEPTED),
        ("Spark XV boots mondo 28.0", SizeVerdict.ACCEPTED),
        ("Spark XV boots US 10", SizeVerdict.ACCEPTED),
        ("Spark XV Boots Gr. 41", SizeVerdict.CONFLICT),
        ("Spark XV boots mondo 26.5", SizeVerdict.CONFLICT),
        ("Deeluxe Spark XV Boots, wenig gefahren", SizeVerdict.ABSENT),
        # UK is a third scale; guessing it as EU would invent a conflict.
        ("Spark XV boots UK 9", SizeVerdict.ABSENT),
        # Board length and retail price must not read as sizes.
        ("Spark XV boots, Board 161 cm dazu, NP 450", SizeVerdict.ABSENT),
    ],
)
def test_size_verdict(text, expected):
    assert size_verdict(text, "shoe", SHOE) is expected


def test_normalise_size_folds_spellings():
    assert normalise_size("27,5") == "27.5"
    assert normalise_size("28.0") == "28"


# --- condition -------------------------------------------------------------

@pytest.mark.parametrize(
    "text, expected",
    [
        ("Boots, Schale defekt", ConditionVerdict.BROKEN),
        ("nur für Teile", ConditionVerdict.BROKEN),
        ("stark gebraucht aber funktionsfähig", ConditionVerdict.HEAVILY_USED),
        ("kaum getragen, wie neu", ConditionVerdict.OK),
    ],
)
def test_condition(text, expected):
    assert condition_of(text) is expected


# --- shipping --------------------------------------------------------------

def test_swiss_listings_always_ship_to_ch():
    raw = listing("Spark XV boots Gr 43", country="CH")

    assert shipping_verdict(raw, raw.haystack, RULES.delivery) is ShippingVerdict.DELIVERABLE


def test_german_listing_needs_an_explicit_promise():
    silent = listing("Spark XV boots Gr 43", country="DE")
    explicit = listing("Spark XV boots Gr 43", "Versand nach Schweiz möglich", country="DE")

    assert shipping_verdict(silent, silent.haystack, RULES.delivery) is ShippingVerdict.UNSTATED
    assert shipping_verdict(explicit, explicit.haystack, RULES.delivery) is ShippingVerdict.DELIVERABLE


# --- landed cost -----------------------------------------------------------

def test_swiss_price_is_the_landed_price():
    assert landed(listing("x", price=200.0), RULES) == 200.0


def test_carrying_in_over_the_allowance_adds_vat_on_everything():
    """EU goods come via the French address and are carried across: VAT on
    the whole value once over the allowance, and no carrier handling fee."""
    raw = listing("x", price=200.0, currency="EUR", country="DE", shipping=15.0)
    goods = 215.0 * 0.94

    assert goods > DELIVERY.import_allowance
    assert landed(raw, RULES) == pytest.approx(round(goods * 1.081, 2))


def test_carrying_in_under_the_allowance_is_vat_free():
    raw = listing("x", price=50.0, currency="EUR", country="DE")

    assert landed(raw, RULES) == pytest.approx(round(50.0 * 0.94, 2))


# --- end to end ------------------------------------------------------------

def test_matching_boot_is_kept():
    outcome, match = evaluate(listing("Deeluxe Spark XV Snowboard Boots Gr. 43"), TARGETS, RULES)

    assert outcome is Outcome.MATCH
    assert match.target is BOOTS
    assert match.flags == []


def test_wrong_size_is_dropped():
    outcome, match = evaluate(listing("Deeluxe Spark XV Boots Gr. 41"), TARGETS, RULES)

    assert outcome is Outcome.WRONG_SIZE
    assert match is None


def test_board_sharing_the_model_name_is_not_a_boot():
    """`context` is what stops a splitboard listing matching a boot target."""
    outcome, _ = evaluate(listing("Spark XV Splitboard 158cm mit Bindung"), TARGETS, RULES)

    assert outcome is Outcome.NO_TARGET


def test_wanted_ads_are_dropped():
    outcome, _ = evaluate(listing("Suche Deeluxe Spark XV Boots Gr 43"), TARGETS, RULES)

    assert outcome is Outcome.WANTED_AD


def test_goggles_ignore_size_entirely():
    outcome, match = evaluate(listing("Anon M4 Skibrille Toric, bright lens"), TARGETS, RULES)

    assert outcome is Outcome.MATCH
    assert match.size is SizeVerdict.ACCEPTED


def test_missing_size_is_kept_but_flagged():
    outcome, match = evaluate(listing("Deeluxe Spark XV Boots, wenig gefahren"), TARGETS, RULES)

    assert outcome is Outcome.MATCH
    assert "size unstated" in match.flags


def test_german_listing_flags_the_shipping_question():
    raw = listing("Deeluxe Spark XV Boots Gr. 43", country="DE", currency="EUR")
    _, match = evaluate(raw, TARGETS, RULES)

    assert "ask about shipping to CH/FR" in match.flags


# --- accessories -----------------------------------------------------------

def test_spare_lens_is_not_a_pair_of_goggles():
    # The body says what it belongs to, as the real tutti listing did.
    raw = listing("Oakley Line Miner persimon Ersatzglas", "Ersatzglas für Skibrille", price=19.0)
    outcome, _ = evaluate(raw, EXAMPLE.targets, RULES)

    assert outcome is Outcome.ACCESSORY


def test_goggles_bundled_with_a_spare_lens_are_kept():
    outcome, _ = evaluate(listing("Anon M4 Skibrille inkl. Ersatzglas"), EXAMPLE.targets, RULES)

    assert outcome is Outcome.MATCH


# --- bare mondo sizes (ski boots) ------------------------------------------

@pytest.mark.parametrize(
    "text, expected",
    [
        # Ski boots are commonly listed as a bare integer mondo.
        ("Scarpa Maestrale 28 Skitourenschuh", SizeVerdict.ACCEPTED),
        ("Skitouren Schuh Zero G Tour 25", SizeVerdict.CONFLICT),
        ("La Sportiva Skorpius CR Skitourenschuhe 27", SizeVerdict.CONFLICT),
        # A range that includes an accepted size still qualifies.
        ("Salomon S-Lab X-Alp Tourenskischuh 27/27.5", SizeVerdict.ACCEPTED),
        # Flex index, ski length and sole length must not read as sizes.
        ("Skischuh Dynafit Hoji Free 130 Skitouren", SizeVerdict.ABSENT),
        ("Tourenski K2 Talkback 88 160cm mit Scarpa F1", SizeVerdict.ABSENT),
        ("Dynafit TLT8 Tourenskischuhe, Groesse 317mm", SizeVerdict.ABSENT),
    ],
)
def test_bare_mondo_sizes(text, expected):
    assert size_verdict(text, "shoe", SHOE) is expected


def test_liner_only_sale_is_an_accessory():
    """A liner sold under the boot's name would set a false price floor."""
    outcome, _ = evaluate(
        listing("Deeluxe Spark XV Innenschuh / inner boots 27.5", price=47.0),
        EXAMPLE.targets,
        RULES,
    )

    assert outcome is Outcome.ACCESSORY


def test_a_boot_described_with_its_liner_is_kept():
    boot = next(t for t in EXAMPLE.targets if t.key == "deeluxe-spark-xv")

    assert not is_accessory("Deeluxe Spark XV mit Innenschuh 28", boot.accessories)


# --- neighbouring gear -----------------------------------------------------

TOURING = Target(
    key="touring-ski-boots",
    label="Backcountry ski boots",
    patterns=["skitouren|tourenschuh"],
    context=["schuh", "boot"],
    exclude=["barryvox|\\blvs\\b", "steigeisen", "kletterschuh", "langlauf"],
    size_scheme="shoe",
    price_policy=PricePolicy.CHEAPEST,
    fallback_max=400.0,
)


@pytest.mark.parametrize(
    "title, body",
    [
        # Each of these was stored as a touring boot: the body mentions the
        # activity, and the word "Schuh" appears somewhere in it.
        ("Mammut Barryvox S LVS Set mit Schaufel", "Skitouren Ausrüstung, Schuhe nicht dabei"),
        ("Steigeisen Black Diamond Neve Pro", "passend für Tourenschuhe, Skitouren"),
        ("Kletterschuhe La Sportiva Scarpa Tenaya", "auch für Skitouren Fans, Schuhe"),
        ("Langlaufschuh", "Skitouren Schuhe Grösse 43"),
    ],
)
def test_neighbouring_gear_is_excluded(title, body):
    outcome, _ = evaluate(listing(title, body), [TOURING], RULES)

    assert outcome is Outcome.NO_TARGET


def test_a_real_touring_boot_still_matches():
    outcome, match = evaluate(
        listing("Skitourenschuh Scarpa Maestrale RS 27.5 (43)"), [TOURING], RULES
    )

    assert outcome is Outcome.MATCH


# --- condition and variant vocabulary from real listings -------------------

def test_missing_buckle_is_broken():
    assert condition_of("Scarpa Maestrale, eine Schnalle fehlt") is ConditionVerdict.BROKEN


def test_viel_gebraucht_is_heavy_use():
    assert condition_of("viel gebraucht, aber alles intakt") is ConditionVerdict.HEAVILY_USED


def test_a_bare_lens_is_not_goggles():
    outcome, _ = evaluate(
        listing("ANON M4S PERCEIVE BRILLENGLAS (TORISCH) Violet S2", price=89.0), EXAMPLE.targets, RULES
    )

    assert outcome is Outcome.ACCESSORY


def test_junior_frames_are_not_your_size():
    outcome, _ = evaluate(listing("Skibrille Junior Oakley Line Miner Youth"), EXAMPLE.targets, RULES)

    assert outcome is Outcome.ACCESSORY


# --- millimetre mondo ("275" = 27.5) ---------------------------------------

@pytest.mark.parametrize(
    "text, expected",
    [
        ("Tourenschuh Scarpa 275", SizeVerdict.ACCEPTED),
        ("Tourenskischuh Groesse 275", SizeVerdict.ACCEPTED),
        ("Scarpa Maestrale 280 Skitourenschuh", SizeVerdict.ACCEPTED),
        ("Skischuh Gr. 280", SizeVerdict.ACCEPTED),
        ("Tourenschuh 265", SizeVerdict.CONFLICT),
        # Money, not millimetres.
        ("Tourenschuh, Neupreis 280.-", SizeVerdict.ABSENT),
        ("Tourenschuh, NP ca. 280", SizeVerdict.ABSENT),
        ("Tourenschuh, verkaufe fuer 275", SizeVerdict.ABSENT),
        # Sole length, not size — the ranges overlap outright.
        ("Dynafit TLT8 Tourenskischuhe, Groesse 317mm", SizeVerdict.ABSENT),
    ],
)
def test_millimetre_mondo_sizes(text, expected):
    assert size_verdict(text, "shoe", SHOE) is expected


def test_millimetre_and_decimal_spellings_are_the_same_size():
    assert normalise_size("275") == normalise_size("27,5") == "27.5"
    assert normalise_size("280") == normalise_size("28.0") == "28"


def test_splitboard_binding_parts_are_not_boots():
    """"Hardboot" contains "boot", so a heel-riser listing passed the context check."""
    cfg = EXAMPLE
    raw = listing("Phantom Rocket Heel Riser Steighilfe Splitboard Hardboot", price=92.0)
    outcome, _ = evaluate(raw, cfg.targets, cfg.rules)

    assert outcome is Outcome.NO_TARGET
