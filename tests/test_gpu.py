"""GPU target and auction-aware pricing.

"Best price" breaks on two things boots never exercised hard: auctions, whose
visible price is an opening bid, and implausibly cheap listings, which on
GPUs are mostly scams.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


from dealhunt import config as cfg_module  # noqa: E402
from dealhunt import cli as run  # noqa: E402
from dealhunt.matching.rules import evaluate  # noqa: E402
from dealhunt.models import Outcome, PriceKind, RawListing  # noqa: E402
from dealhunt.sources import ricardo  # noqa: E402
from dealhunt.store.db import Store  # noqa: E402

from conftest import EXAMPLE as CFG  # noqa: E402
GPU_KEY = "gpu-24gb"


def listing(title, price=650.0, kind=PriceKind.FIXED):
    return RawListing(
        source="test", listing_id=title[:20], title=title, url="https://example.test",
        country="CH", price=price, price_kind=kind,
    )


def match_of(title, **kwargs):
    return evaluate(listing(title, **kwargs), CFG.targets, CFG.rules)


# --- which cards count -----------------------------------------------------

@pytest.mark.parametrize("title", [
    "Zotac RTX 3090 Trinity 24GB",
    "MSI GeForce RTX 3090 Ti Suprim X",
    "ASUS TUF RTX 4090 OC 24 GB",
    "Nvidia RTX 5090 Founders Edition",
    "Grafikkarte Palit 3090 GamingPro",
])
def test_24gb_nvidia_cards_match(title):
    outcome, match = match_of(title)

    assert outcome is Outcome.MATCH
    assert match.target.key == GPU_KEY


@pytest.mark.parametrize("title", [
    # 16 GB or less: faster at games, useless for the brief.
    "MSI RTX 4080 Super 16GB",
    "RTX 3080 Ti 12GB Grafikkarte",
    # A laptop 4090 performs like a desktop 3080.
    "Gaming Laptop RTX 4090 i9",
    "Notebook mit RTX 3090 mobile",
    # The price is for something else.
    "Gaming PC mit RTX 3090 und Ryzen 9",
    "RTX 3090 OVP nur Karton",
    "EKWB Wasserkühler für RTX 3090",
    "Ersatzlüfter für RTX 4090",
    "GPU Halterung Stütze RTX 4090",
])
def test_everything_else_does_not(title):
    _, match = match_of(title)

    assert match is None or match.target.key != GPU_KEY


# --- auctions --------------------------------------------------------------

def test_ricardo_buy_now_is_a_fixed_price():
    raw = ricardo._to_listing({"id": "1", "title": "RTX 3090", "bidPrice": 50, "buyNowPrice": 690}, {})

    assert raw.price == 690
    assert raw.price_kind is PriceKind.FIXED


def test_ricardo_auction_without_buy_now_is_a_bid():
    raw = ricardo._to_listing({"id": "1", "title": "RTX 3090", "bidPrice": 50, "buyNowPrice": None}, {})

    assert raw.price == 50
    assert raw.price_kind is PriceKind.AUCTION


def test_an_auction_is_flagged_as_a_current_bid():
    _, match = match_of("Zotac RTX 3090 Trinity 24GB", price=50.0, kind=PriceKind.AUCTION)

    assert any("auction" in f for f in match.flags)


def test_an_implausibly_cheap_card_is_flagged():
    _, match = match_of("ASUS TUF RTX 4090 OC 24 GB", price=250.0)

    assert any("implausibly cheap" in f for f in match.flags)


# --- calibration must not learn from bids or bait -------------------------

def test_calibration_ignores_auction_bids(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    _, fixed = match_of("Zotac RTX 3090 Trinity 24GB", price=700.0)
    _, auction = match_of("MSI RTX 3090 Ventus", price=40.0, kind=PriceKind.AUCTION)
    store.record(fixed)
    store.record(auction)

    assert store.prices_for(GPU_KEY) == [700.0]


def test_calibration_ignores_implausibly_cheap_prices():
    gpu = next(t for t in CFG.targets if t.key == GPU_KEY)

    assert run._calibration_prices(gpu, [120.0, 640.0, 700.0]) == [640.0, 700.0]


# --- title-only matching ---------------------------------------------------

def test_a_description_mentioning_a_3090_does_not_make_a_4080_one():
    raw = listing("MSI RTX 4080 Super 16GB")
    raw.description = "Deutlich schneller als meine alte RTX 3090"
    _, match = evaluate(raw, CFG.targets, CFG.rules)

    assert match is None or match.target.key != GPU_KEY


def test_a_real_card_is_not_excluded_by_its_description():
    raw = listing("Zotac RTX 3090 Trinity 24GB")
    raw.description = "War im PC verbaut, Lüfter leise, Kabel und OVP dabei"
    outcome, match = evaluate(raw, CFG.targets, CFG.rules)

    assert outcome is Outcome.MATCH
    assert match.target.key == GPU_KEY


@pytest.mark.parametrize("title", [
    "ASUS RTX 4090 TUF inkl. 12VHPWR Adapter",
    "Zotac RTX 3090 mit Originalkarton",
])
def test_a_card_listed_with_its_accessories_still_matches(title):
    _, match = match_of(title)

    assert match is not None and match.target.key == GPU_KEY


@pytest.mark.parametrize("title", [
    "12VHPWR Kabel für RTX 4090",
    "Adapter für Nvidia 4090",
])
def test_a_part_for_a_card_is_not_a_card(title):
    _, match = match_of(title, price=25.0)

    assert match is None or match.target.key != GPU_KEY


# --- real noise from the first live run (2026-09-26) -----------------------

@pytest.mark.parametrize("title", [
    # Wanted ads, the word anywhere in the title.
    "Nvidia RTX 3090 gesucht – alle Hersteller – schnelle Abwicklung",
    "70825 Korntal-Münchingen 23.09.2026 suche RTX 4090 24GB Ich suche eine",
    # A water block named after the card it fits.
    "EK Quantum Vector² RTX 4090 ZOTAC Trinity/AMP",
    # Whole machines: desktops and laptops, recognisable by CPU, RAM, SSD, screen.
    "OMEN by HP 45L Gaming Desktop, Ultra 9, RTX 5090, 64GB RAM",
    "XMG/PCspecialist i9-13900HX, 16GB RTX 4090, 64GB DDR5",
    'ASUS ROG Zephyrus GU605MY 16", Ultra 9 185H RTX 4090 2TB SSD',
    # The box of bits that came with a card.
    "Original Gainward RTX 5090 Phantom Zubehör Set: 12VHPWR",
])
def test_first_live_run_noise_is_rejected(title):
    _, match = match_of(title)

    assert match is None or match.target.key != GPU_KEY


@pytest.mark.parametrize("title", [
    "MSI RTX 3090 SUPRIM 24GB | Ideal für KI, 4K & Rendering",
    "KFA2 GeForce RTX 3090 SG 24GB GDDR6X Grafikkarte mit OVP",
    "ASUS TUF Gaming RTX 3090 OC 24GB GDDR6X | OVP | kaum benutzt",
])
def test_first_live_run_real_cards_still_match(title):
    _, match = match_of(title)

    assert match is not None and match.target.key == GPU_KEY


# --- below the plausibility floor ------------------------------------------

def test_a_fixed_price_below_the_floor_is_not_pushed(tmp_path):
    """Every sub-floor fixed price in the first live run was a part or bait."""
    from dealhunt.models import Mode

    store = Store(tmp_path / "t.sqlite")
    _, bait = match_of("ASUS TUF RTX 4090 OC 24 GB", price=106.0)

    assert run._handle(bait, store, Mode.ALERT, {}, dry_run=False) is None


def test_an_auction_below_the_floor_is_still_pushed(tmp_path):
    """An opening bid is a real chance at a low price, and it is flagged."""
    from dealhunt.models import Mode

    store = Store(tmp_path / "t.sqlite")
    _, auction = match_of("Zotac RTX 3090 Trinity 24GB", price=50.0, kind=PriceKind.AUCTION)

    assert run._handle(auction, store, Mode.ALERT, {}, dry_run=False) is not None


# --- second live run (2026-09-26 18:17) ------------------------------------

def test_a_lesser_card_that_names_a_3090_is_not_one():
    """"*Nicht 3090": the seller named the card it is not."""
    _, match = match_of("Nvidia RTX 3080 20GB VRAM Grafikkarte AI LLM *Nicht 3090", price=700.0)

    assert match is None or match.target.key != GPU_KEY


@pytest.mark.parametrize("title, price, suspicious", [
    # A new 4090 does not sell for a 3090's price.
    ("Ceforce RTX 4090 neuve", 748.0, True),
    ("ASUS TUF RTX 4090 OC", 1700.0, False),
    ("MSI RTX 5090 Gaming Trio", 1200.0, True),
    ("MSI RTX 5090 Gaming Trio", 2600.0, False),
    # The 3090 floor is unchanged.
    ("Zotac RTX 3090 Trinity", 380.0, True),
    ("Zotac RTX 3090 Trinity", 900.0, False),
])
def test_the_plausibility_floor_follows_the_model(title, price, suspicious):
    _, match = match_of(title, price=price)

    assert any("implausibly cheap" in f for f in match.flags) is suspicious
