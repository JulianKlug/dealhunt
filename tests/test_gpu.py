"""GPU targets (RTX 5090, A6000, 6000 Ada, PRO 6000 Blackwell), and the
auction- and scam-aware pricing they rely on.

Titles are real, from tutti.ch and kleinanzeigen.de on 2026-09-26 and
2026-09-28. Each "not a card" case was a real listing that matched an
earlier version of these targets.
"""

from __future__ import annotations

import pytest

from conftest import EXAMPLE as CFG
from dealhunt import cli as run
from dealhunt.matching.rules import evaluate
from dealhunt.models import Mode, Outcome, PriceKind, RawListing
from dealhunt.sources import ricardo
from dealhunt.store.db import Store


def listing(title, price=5000.0, kind=PriceKind.FIXED, country="CH"):
    return RawListing(source="test", listing_id=title[:20], title=title, url="https://example.test",
                      country=country, price=price, price_kind=kind)


def match_of(title, **kwargs):
    return evaluate(listing(title, **kwargs), CFG.targets, CFG.rules)


def key_of(title, **kwargs):
    _, match = match_of(title, **kwargs)
    return match.target.key if match else None


# --- which card is which --------------------------------------------------

@pytest.mark.parametrize("title, key", [
    ("ASUS ROG ASTRAL GeForce RTX 5090 OC 32 GB, GDDR7", "gpu-5090"),
    ("Gainward Rtx 5090 Phantom GS 32 GB, Grafikkarte, LLM, Gaming", "gpu-5090"),
    ("AORUS GeForce RTX 5090 STEALTH ICE 32G / Neuwertig in OVP", "gpu-5090"),
    ("NVIDIA RTX A6000 48GB GDDR6 Workstation GPU OVP Erstbesitz", "gpu-a6000"),
    ("PNY NVIDIA RTX 6000 Ada Generation – 48 GB GDDR6 – Neu + Rechnung", "gpu-6000-ada"),
    ("NVIDIA RTX 6000 Ada 48GB - NEU - Profi-GPU für KI, Rendering- CAD", "gpu-6000-ada"),
    # Sold without the "Ada": 48 GB on a plain "RTX 6000" can only be the Ada.
    ("NVIDIA RTX 6000 48GB GDDR6 Grafikkarte", "gpu-6000-ada"),
    ("Nvidia RTX Pro 6000 Blackwell", "gpu-pro-6000"),
    # "Max-Q Workstation Edition" is the 300 W desktop card, not a laptop chip.
    ("NVIDIA RTX PRO 6000 Blackwell Max-Q Workstation 96GB", "gpu-pro-6000"),
    ("NVIDIA RTX Pro 6000  Blackwell MAX-Q Workstation Edition", "gpu-pro-6000"),
])
def test_each_card_reaches_its_own_target(title, key):
    assert key_of(title) == key


@pytest.mark.parametrize("title", [
    # No longer hunted.
    "Zotac RTX 3090 Trinity 24GB",
    "ASUS TUF RTX 4090 OC 24 GB",
    # The 2018 Quadro RTX 6000: 24 GB, slower than a 3090.
    "NVIDIA Quadro RTX 6000 24GB",
    "MSI RTX 5080 16GB Gaming Trio",
])
def test_other_cards_are_not_hunted(title):
    assert key_of(title) is None


@pytest.mark.parametrize("title", [
    # Laptops: a laptop 5090 is not a desktop 5090.
    "Acer Predator Helios 16 AI OLED RTX 5090 Gaming Laptop",
    "Razer Blade 16 RTX 5090, 64 GB RAM 4 TB SSD| und Razer Kühler",
    "Lenovo Legion Pro 7 16IAX10H Ultra 9 275HX/64GB/1TB/RTX5090 *TOP*",
    # Whole machines, recognisable by a CPU, RAM or storage.
    "High-End Gaming PC RTX 5090, Ryzen 9 9950X3D, 64GB DDR5, 2TB",
    "MSI MEG Vision X AI RTX 5090 | 128GB DDR5 RAM *NEU & OVP*",
    "K.I. Workstation Asus RTX 5090 Astral Ryzen 64GB",
    "RTX A6000 48GB + 512GB ECC RAM Workstation",
    "AI Workstation / Threadripper 3990X / RTX A6000 48GB / 256GB RAM",
    "AI Workstation RTX PRO 6000 Blackwell 96GB Threadripper 9970X",
    "PRO Workstation 24 Kern Ultra 9, 72GB Blackwell, 128GB DDR5",
    # An empty box, and parts named after the card.
    "Leerkarton Gigabyte AORUS MASTER RTX 5090 – ohne Grafikkarte",
    "Original Gainward RTX 5090 Phantom Zubehör Set: 12VHPWR",
    "12VHPWR Kabel für RTX 5090",
])
def test_machines_boxes_and_parts_are_not_cards(title):
    assert key_of(title, price=500.0) is None


@pytest.mark.parametrize("title", [
    "Suche RTX 5090 GPU Ankauf Nvidia",
    "Kaufe RTX 4090 / RTX 5090 & High-End Gaming PCs",
    "✨ Suche | Wir kaufen: GAMING PC - Ryzen Intel RTX GTX AMD SSD M2 ULTRA Aio Was",
    "RTX A2000 A4000 A5000 A6000 gesucht",
    "SUCHE RTX PRO 6000 Blackwell Server 96GB – bis 15.000€",
    "Ankauf RTX 5090 – sofort Bargeld",
])
def test_wanted_ads_are_dropped(title):
    outcome, _ = match_of(title)

    assert outcome is Outcome.WANTED_AD


# --- plausibility: a price far below the market is bait -------------------

@pytest.mark.parametrize("title, price, suspicious", [
    ("MSI GeForce RTX 5090 GAMING TRIO OC", 1800.0, True),
    ("MSI GeForce RTX 5090 GAMING TRIO OC", 4500.0, False),
    ("NVIDIA RTX A6000 48GB GDDR6", 1500.0, True),
    ("NVIDIA RTX A6000 48GB GDDR6", 4200.0, False),
    ("PNY NVIDIA RTX 6000 Ada 48GB", 2500.0, True),
    ("PNY NVIDIA RTX 6000 Ada 48GB", 6300.0, False),
    ("Nvidia RTX Pro 6000 Blackwell", 4000.0, True),
    ("Nvidia RTX Pro 6000 Blackwell", 11000.0, False),
])
def test_each_card_has_its_own_plausibility_floor(title, price, suspicious):
    _, match = match_of(title, price=price)

    assert any("implausibly cheap" in f for f in match.flags) is suspicious


def test_each_card_is_priced_against_its_own_market():
    """Pooled, a PRO 6000 30% under its market would still be far above
    any 5090, and never reach the buyer."""
    bars = {t.key: t.fallback_max for t in CFG.targets if t.key.startswith("gpu-")}

    assert set(bars) == {"gpu-5090", "gpu-a6000", "gpu-6000-ada", "gpu-pro-6000"}
    # A PRO 6000 at 30% under its own bar would still fail a pooled 5090 bar.
    a_real_deal = bars["gpu-pro-6000"] * 0.7

    assert a_real_deal > bars["gpu-5090"]


# --- auctions and the push rule (card-independent) ------------------------

def test_ricardo_buy_now_is_a_fixed_price():
    raw = ricardo._to_listing({"id": "1", "title": "RTX 5090", "bidPrice": 50, "buyNowPrice": 4600}, {})

    assert raw.price == 4600 and raw.price_kind is PriceKind.FIXED


def test_ricardo_auction_without_buy_now_is_a_bid():
    raw = ricardo._to_listing({"id": "1", "title": "RTX 5090", "bidPrice": 50, "buyNowPrice": None}, {})

    assert raw.price == 50 and raw.price_kind is PriceKind.AUCTION


def test_an_auction_is_flagged_as_a_current_bid():
    _, match = match_of("MSI GeForce RTX 5090 GAMING TRIO OC", price=900.0, kind=PriceKind.AUCTION)

    assert any("auction" in f for f in match.flags)


def test_a_fixed_price_below_the_floor_is_not_pushed(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    _, bait = match_of("MSI GeForce RTX 5090 GAMING TRIO OC", price=1200.0)

    assert run._handle(bait, store, Mode.ALERT, {}, dry_run=False) is None


def test_an_auction_below_the_floor_is_still_pushed(tmp_path):
    """An opening bid is a real chance at a low price, and it is flagged."""
    store = Store(tmp_path / "t.sqlite")
    _, auction = match_of("MSI GeForce RTX 5090 GAMING TRIO OC", price=900.0, kind=PriceKind.AUCTION)

    assert run._handle(auction, store, Mode.ALERT, {}, dry_run=False) is not None


def test_calibration_ignores_auction_bids(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    _, fixed = match_of("MSI GeForce RTX 5090 GAMING TRIO OC", price=4600.0)
    _, auction = match_of("ASUS RTX 5090 TUF OC 32GB", price=900.0, kind=PriceKind.AUCTION)
    store.record(fixed)
    store.record(auction)

    assert store.prices_for("gpu-5090") == [4600.0]


def test_calibration_ignores_implausibly_cheap_prices():
    gpu = next(t for t in CFG.targets if t.key == "gpu-5090")

    assert run._calibration_prices(gpu, [1200.0, 4400.0, 4800.0]) == [4400.0, 4800.0]


# --- title-only matching --------------------------------------------------

def test_a_description_mentioning_a_5090_does_not_make_a_4090_one():
    raw = listing("MSI RTX 4090 Suprim 24GB")
    raw.description = "Fast so schnell wie eine RTX 5090"
    _, match = evaluate(raw, CFG.targets, CFG.rules)

    assert match is None


def test_a_real_card_is_not_excluded_by_its_description():
    raw = listing("MSI GeForce RTX 5090 GAMING TRIO OC")
    raw.description = "War im PC verbaut, Lüfter leise, Kabel und OVP dabei"
    _, match = evaluate(raw, CFG.targets, CFG.rules)

    assert match is not None and match.target.key == "gpu-5090"
