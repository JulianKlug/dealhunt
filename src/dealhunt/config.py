"""Loads the buyer's config and the target files it lists into typed objects.

Two kinds of file, deliberately separate:

    dealhunt.toml          the buyer: where they live, where they can receive,
                           their sizes, their alert topic. Personal, not shared.
    targets/*.toml         what to hunt: item patterns, exclusions, price bars,
                           search queries. Contains nothing personal, so a
                           target file can be shared and reused as it is.

Nothing below this module ever sees a dict or a raw string from either file.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

try:
    import tomllib as toml  # Python 3.11+
except ImportError:  # pragma: no cover
    import tomli as toml

from dealhunt.matching.rules import Delivery, RuleSet
from dealhunt.matching.sizes import NORMALISERS, SCHEMES
from dealhunt.models import Mode, PricePolicy, SearchQuery, SizeSpec, Target, TextScope


class ConfigError(ValueError):
    """The config is inconsistent in a way that would fail at run time."""


@dataclass(frozen=True)
class Config:
    mode: Mode
    ntfy_topic: str
    currency: str
    db_path: Path
    thresholds_path: Path
    enabled_sources: List[str]
    source_intervals_min: Dict[str, int]
    curl_binary: str
    queries: List[SearchQuery]
    targets: List[Target]
    rules: RuleSet
    min_samples: int
    percentile: int
    cheapest_percentile: int


def load(path: Path) -> Config:
    raw = _read(path)
    root = path.parent

    targets: List[Target] = []
    for target_file in raw["target_files"]:
        targets.extend(_read_targets(_resolve(root, target_file)))

    rules = _to_ruleset(raw)
    _check_sizes(targets, rules)

    calibration = raw["calibration"]
    pages = raw.get("search", {}).get("pages", 1)

    return Config(
        mode=Mode(raw["mode"]),
        ntfy_topic=raw["ntfy_topic"],
        currency=raw["delivery"]["currency"].upper(),
        db_path=_resolve(root, raw["db_path"]),
        thresholds_path=_resolve(root, raw["thresholds_path"]),
        enabled_sources=list(raw["sources"]["enabled"]),
        source_intervals_min=dict(raw["sources"].get("min_interval_min", {})),
        curl_binary=raw.get("net", {}).get("curl_binary", ""),
        queries=_queries(targets, pages, raw.get("search", {}).get("extra_queries", [])),
        targets=targets,
        rules=rules,
        min_samples=calibration["min_samples"],
        percentile=calibration["percentile"],
        cheapest_percentile=calibration.get("cheapest_percentile", 10),
    )


def load_thresholds(path: Path) -> Dict[str, float]:
    """Calibrated alert bars, empty until `--calibrate` has run."""
    if not path.exists():
        return {}

    return json.loads(path.read_text())


def save_thresholds(path: Path, thresholds: Dict[str, float]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(thresholds, indent=2, sort_keys=True))


def _read(path: Path) -> dict:
    with path.open("rb") as handle:
        return toml.load(handle)


def _read_targets(path: Path) -> List[Target]:
    """A target file: optional file-wide [defaults], then [[targets]].

    Defaults save repeating what every target in a file shares, such as the
    context words and spare parts of "snowboard boots". A target's own value
    replaces the default; lists are not merged.
    """
    raw = _read(path)
    defaults = raw.get("defaults", {})

    return [_to_target({**defaults, **entry}, path) for entry in raw.get("targets", [])]


def _to_target(raw: dict, origin: Path) -> Target:
    try:
        return Target(
            key=raw["key"],
            label=raw["label"],
            patterns=list(raw["patterns"]),
            context=list(raw.get("context", [])),
            exclude=list(raw.get("exclude", [])),
            price_policy=PricePolicy(raw.get("price_policy", PricePolicy.VALUE.value)),
            fallback_max=float(raw["fallback_max"]),
            size_scheme=raw.get("size_scheme"),
            accessories=tuple(raw.get("accessories", [])),
            title_exclude=tuple(raw.get("title_exclude", [])),
            min_plausible=float(raw["min_plausible"]) if "min_plausible" in raw else None,
            match_scope=TextScope(raw.get("match_scope", TextScope.FULL.value)),
            plausible_floors={k: float(v) for k, v in raw.get("plausible_floors", {}).items()} or None,
            queries=tuple(raw.get("queries", [])),
        )
    except KeyError as missing:
        raise ConfigError(f"{origin.name}: target {raw.get('key', '?')!r} lacks {missing}") from None


def _to_ruleset(raw: dict) -> RuleSet:
    delivery = raw["delivery"]

    return RuleSet(
        delivery=Delivery(
            home_country=delivery["home_country"].upper(),
            addresses=frozenset(c.upper() for c in delivery["addresses"]),
            import_vat_rate=float(delivery.get("import_vat_rate", 0.0)),
            import_allowance=float(delivery.get("import_allowance", 0.0)),
            ships_to=tuple(delivery.get("ships_to_phrases", [])),
        ),
        fx={k.upper(): float(v) for k, v in raw.get("fx", {}).items()},
        sizes={scheme: _to_size_spec(scheme, spec) for scheme, spec in raw.get("sizes", {}).items()},
    )


def _to_size_spec(scheme: str, raw: dict) -> SizeSpec:
    if scheme not in SCHEMES:
        raise ConfigError(f"unknown size scheme {scheme!r}; known: {sorted(SCHEMES)}")

    normalise = NORMALISERS[scheme]

    return SizeSpec(
        accept=frozenset(normalise(str(v)) for v in raw["accept"]),
        accept_alt=frozenset(normalise(str(v)) for v in raw.get("accept_alt", [])),
    )


def _check_sizes(targets: List[Target], rules: RuleSet) -> None:
    """Fail at load, not mid-run, when a sized target meets a buyer without sizes."""
    for target in targets:
        if target.size_scheme and target.size_scheme not in rules.sizes:
            raise ConfigError(
                f"target {target.key!r} is sized in {target.size_scheme!r}, "
                f"but the config has no [sizes.{target.size_scheme}]"
            )


def _queries(targets: List[Target], pages: int, extra: List[str]) -> List[SearchQuery]:
    """Every target's queries plus the buyer's own, deduplicated, in order.

    The buyer's extras are for searches too personal for a shared target
    file, such as a size: "snowboard boots 43".
    """
    seen: Dict[str, None] = {}

    for target in targets:
        for text in target.queries:
            seen.setdefault(text, None)

    for text in extra:
        seen.setdefault(text, None)

    return [SearchQuery(text=text, pages=pages) for text in seen]


def _resolve(root: Path, value: str) -> Path:
    candidate = Path(value)

    return candidate if candidate.is_absolute() else root / candidate
