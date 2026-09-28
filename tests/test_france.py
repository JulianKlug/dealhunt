"""French sources and delivery via a French address.

Fixtures are real Vinted accessibility labels and hardware.fr topic titles
captured on 2026-09-26.
"""

from __future__ import annotations

import pytest

from dealhunt.matching.rules import (  # noqa: E402
    condition_of,
    evaluate,
    landed,
    shipping_verdict,
)
from dealhunt.models import ConditionVerdict, Outcome, RawListing, ShippingVerdict  # noqa: E402
from dealhunt.sources import hardware_fr, vinted  # noqa: E402

from conftest import EXAMPLE as CFG  # noqa: E402

VAT_RATE = CFG.rules.delivery.import_vat_rate
ALLOWANCE = CFG.rules.delivery.import_allowance


def listing(title, price=100.0, currency="EUR", country="FR", description=""):
    return RawListing(source="t", listing_id="1", title=title, url="https://x.test",
                      country=country, price=price, currency=currency, description=description)


# --- Vinted label parsing --------------------------------------------------

VINTED_HTML = """
<a href="https://www.vinted.fr/items/10143308696-scarponi-snowboard-northwawe?referrer=catalog"
   title="Scarponi snowboard Northwawe, Marque: NORTHWAVE, État: Très bon état, Taille: 42,5, 35.00 €, 37.45 €"></a>
<a href="https://www.vinted.fr/items/10143308696-scarponi-snowboard-northwawe?referrer=catalog"
   title="duplicate link to the same item"></a>
<a href="https://www.vinted.fr/items/10129728674-slingshot-copy-cat"
   title="Slingshot - Copy Cat '24 - Women's Wake Binding - US Women's 7 (EU 37.5), État: Neuf avec étiquette, Taille: 37.5, 444.00 €, 466.90 €"></a>
<a href="https://www.vinted.fr/member/123" title="a seller, not an item"></a>
"""


def test_vinted_parses_each_item_once():
    listings = vinted.parse(VINTED_HTML)

    assert [l.listing_id for l in listings] == ["10143308696", "10129728674"]


def test_vinted_splits_title_from_attributes():
    first = vinted.parse(VINTED_HTML)[0]

    assert first.title == "Scarponi snowboard Northwawe"
    assert "Taille: 42,5" in first.description
    assert "Très bon état" in first.description


def test_vinted_price_includes_buyer_protection():
    """The second price is what you actually pay."""
    assert vinted.parse(VINTED_HTML)[0].price == 37.45


def test_vinted_titles_without_a_brand_still_split():
    assert vinted.parse(VINTED_HTML)[1].title.startswith("Slingshot - Copy Cat")


def test_vinted_listings_are_french_deliveries():
    assert vinted.parse(VINTED_HTML)[0].country == "FR"


def test_vinted_urls_drop_tracking_parameters():
    assert vinted.parse(VINTED_HTML)[0].url == "https://www.vinted.fr/items/10143308696-scarponi-snowboard-northwawe"


def test_vinted_search_url():
    assert vinted.search_url("boots snowboard") == (
        "https://www.vinted.fr/catalog?search_text=boots+snowboard&order=newest_first"
    )


# --- hardware.fr -----------------------------------------------------------

HFR_HTML = """
<a href="/hfr/AchatsVentes/Hardware/rtx-5090-fe_sujet_1_1.htm" class="cCatTopic" title="x">[VDS - 75] RTX 5090 FE 32Go 3 950€</a>
<a href="/hfr/AchatsVentes/Hardware/ach-gpu_sujet_2_1.htm" class="cCatTopic" title="x">[ACH] RTX 3090 max 600 euros</a>
<a href="/hfr/AchatsVentes/Hardware/ech_sujet_3_1.htm" class="cCatTopic" title="x">[ECH] 4090 contre 2x3090</a>
<a href="/hfr/AchatsVentes/Hardware/vds-4090_sujet_4_1.htm" class="cCatTopic" title="x">[VDS] Gigabyte RTX 4090 Gaming OC - 1 450 euros</a>
<a href="/hfr/AchatsVentes/Hardware/vds-ram_sujet_5_1.htm" class="cCatTopic" title="x">[VDS] kit DDR4 2 x 16 go Gskill</a>
"""


def test_hardware_fr_keeps_only_sale_topics():
    titles = [l.title for l in hardware_fr.parse(HFR_HTML)]

    assert all("[ACH]" not in t and "[ECH]" not in t for t in titles)
    assert len(titles) == 3


def test_hardware_fr_reads_the_price_from_the_title():
    by_title = {l.title: l for l in hardware_fr.parse(HFR_HTML)}

    assert by_title["[VDS - 75] RTX 5090 FE 32Go 3 950€"].price == 3950.0
    assert by_title["[VDS] Gigabyte RTX 4090 Gaming OC - 1 450 euros"].price == 1450.0
    assert by_title["[VDS] kit DDR4 2 x 16 go Gskill"].price is None


def test_hardware_fr_reads_the_department():
    first = hardware_fr.parse(HFR_HTML)[0]

    assert first.location == "dept 75"
    assert first.country == "FR"


def test_a_hardware_fr_card_matches_the_gpu_target():
    first = hardware_fr.parse(HFR_HTML)[0]
    outcome, match = evaluate(first, CFG.targets, CFG.rules)

    assert outcome is Outcome.MATCH and match.target.key == "gpu-5090"


# --- delivery via France, carried into Switzerland -------------------------

def test_french_sellers_deliver_to_the_french_address():
    raw = listing("Deeluxe Spark XV Taille 43")

    assert shipping_verdict(raw, raw.haystack, CFG.rules.delivery) is ShippingVerdict.DELIVERABLE


def test_german_sellers_shipping_to_france_count():
    raw = listing("Spark XV Gr 43", country="DE", description="Versand nach Frankreich möglich")

    assert shipping_verdict(raw, raw.haystack, CFG.rules.delivery) is ShippingVerdict.DELIVERABLE


def test_the_shipping_flag_names_both_addresses():
    raw = listing("Deeluxe Spark XV Snowboard Boots Gr. 43", country="DE")
    _, match = evaluate(raw, CFG.targets, CFG.rules)

    assert "ask about shipping to CH/FR" in match.flags


def test_carried_in_under_the_allowance_pays_no_vat():
    raw = listing("x", price=100.0, currency="CHF")

    assert landed(raw, CFG.rules) == 100.0


def test_carried_in_over_the_allowance_pays_vat_on_the_whole_value():
    raw = listing("x", price=1000.0, currency="CHF")

    assert landed(raw, CFG.rules) == pytest.approx(1000.0 * (1 + VAT_RATE))


def test_there_is_no_carrier_handling_fee_on_a_carry_in():
    raw = listing("x", price=ALLOWANCE + 50, currency="CHF", country="DE")

    assert landed(raw, CFG.rules) == pytest.approx((ALLOWANCE + 50) * (1 + VAT_RATE))


# --- French vocabulary -----------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("État: Satisfaisant", ConditionVerdict.HEAVILY_USED),
    ("très usé mais fonctionnel", ConditionVerdict.HEAVILY_USED),
    ("vendu pour pièces, ne fonctionne pas", ConditionVerdict.BROKEN),
    ("carte HS", ConditionVerdict.BROKEN),
    ("État: Très bon état", ConditionVerdict.OK),
])
def test_french_condition_words(text, expected):
    assert condition_of(text) is expected


@pytest.mark.parametrize("title", [
    "Recherche RTX 3090",
    "Cherche boots snowboard 43",
    "Boots de snowboard enfant Wed'ze Indy 300",
    "Scarponi snowboard Salomon bambino",
    "Écran de rechange Anon M4",
])
def test_french_wanted_children_and_spares_are_dropped(title):
    _, match = evaluate(listing(title, description="Taille: 43"), CFG.targets, CFG.rules)

    assert match is None


def test_a_french_goggle_listing_matches():
    outcome, match = evaluate(listing("Masque de ski Smith Squad écran ChromaPop"), CFG.targets, CFG.rules)

    assert outcome is Outcome.MATCH and match.target.key == "smith-squad"


def test_vinted_relative_links_become_absolute():
    """The live rendered DOM uses relative hrefs; the first fixture did not."""
    html = ('<a href="/items/10075715132-smith-squad?referrer=catalog" '
            'title="Smith Squad Photo ChromaPop, Marque: Smith, État: Bon état, 65.00 €, 69.10 €"></a>')

    assert vinted.parse(html)[0].url == "https://www.vinted.fr/items/10075715132-smith-squad"


@pytest.mark.parametrize("title", [
    "Je vends ma boîte carte graphique 5090",
    "Scatola originale rtx 5090",
])
def test_an_empty_card_box_is_not_a_card(title):
    _, match = evaluate(listing(title, price=25.0), CFG.targets, CFG.rules)

    assert match is None or not match.target.key.startswith("gpu-")


def test_an_egpu_box_with_a_card_inside_still_counts():
    _, match = evaluate(listing("Aorus RTX 5090 Gaming Box – 32GB", price=4400.0), CFG.targets, CFG.rules)

    assert match is not None and match.target.key == "gpu-5090"
