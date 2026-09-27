"""Shared test fixtures.

EXAMPLE is the shipped example config with its target files: tests that
exercise real vocabulary (model names, spare parts, exclusions) run against
exactly what someone replicating dealhunt starts from.
"""

from __future__ import annotations

from pathlib import Path

from dealhunt import config as cfg_module
from dealhunt.matching.rules import Delivery, RuleSet
from dealhunt.models import SizeSpec

REPO = Path(__file__).resolve().parents[1]

EXAMPLE = cfg_module.load(REPO / "dealhunt.example.toml")

# A buyer in Switzerland who can also receive in France, sized EU 43.
DELIVERY = EXAMPLE.rules.delivery
SHOE = SizeSpec(accept=frozenset({"43", "27.5", "28"}), accept_alt=frozenset({"9.5", "10"}))

RULES = RuleSet(
    delivery=Delivery(
        home_country="CH",
        addresses=frozenset({"CH", "FR"}),
        import_vat_rate=DELIVERY.import_vat_rate,
        import_allowance=DELIVERY.import_allowance,
        ships_to=DELIVERY.ships_to,
    ),
    fx={"CHF": 1.0, "EUR": 0.94},
    sizes={"shoe": SHOE},
)
