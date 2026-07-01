"""Tests for the §3 config schema and its fail-fast validation.

Ported from instance #1's config tests, generalized to the class config surface.
The key property (AC): `from_mapping` reports **every** missing required field
at once, so a misconfigured instance fails fast with one clear message.
"""

from decimal import Decimal

import pytest

from bookkeeper.config import ATTRIBUTION_SKILL, BookkeeperConfig, ConfigError

# A complete, valid §3 mapping — every required field present.
_VALID = {
    "chart_of_accounts": ("5000-supplies", "6000-transport"),
    "accounting_method": "cash",
    "jurisdiction": "XX",
    "tax_regime": "standard",
    "accountant_format": "generic-export",
    "attribution_targets": ("target-001", "target-002"),
    "books_location": "generic-ledger",
    "intake_channel": "generic-channel",
}


def test_from_mapping_builds_a_valid_config():
    cfg = BookkeeperConfig.from_mapping(_VALID)
    assert isinstance(cfg, BookkeeperConfig)
    assert cfg.accounting_method == "cash"
    assert cfg.attribution_targets == ("target-001", "target-002")


def test_missing_required_reports_all_at_once():
    """The core AC: only one field supplied → every other required field reported."""
    with pytest.raises(ConfigError) as excinfo:
        BookkeeperConfig.from_mapping({"chart_of_accounts": ("5000",)})

    msg = str(excinfo.value)
    for field in (
        "accounting_method",
        "jurisdiction",
        "tax_regime",
        "accountant_format",
        "attribution_targets",
        "books_location",
        "intake_channel",
    ):
        assert field in msg
    # The field we did supply must NOT be reported missing.
    assert "chart_of_accounts" not in msg


def test_blank_and_empty_collection_treated_as_missing():
    """A blank string and an empty collection both count as unset."""
    data = dict(_VALID)
    data["intake_channel"] = "   "  # whitespace-only
    data["attribution_targets"] = ()  # empty registry
    with pytest.raises(ConfigError) as excinfo:
        BookkeeperConfig.from_mapping(data)
    msg = str(excinfo.value)
    assert "intake_channel" in msg
    assert "attribution_targets" in msg


def test_optional_boundary_fields_default_to_inert():
    """Boundary fields are optional; unset leaves the boundary inert/conservative."""
    cfg = BookkeeperConfig.from_mapping(_VALID)
    assert cfg.confidence_thresholds == {}
    assert cfg.materiality_floor is None
    assert cfg.owner_policies == {}
    assert cfg.prior_period_state is None


def test_materiality_floor_coerced_to_exact_decimal_from_float():
    """A float `materiality_floor` is coerced to an *exact* Decimal (via str), not lossy.

    `flagAnomaly` compares the floor against `Decimal` amounts, so it must be
    `Decimal` (a Decimal/float compare raises). Coercion goes through `str()`, so a
    float like 1000.10 becomes the exact `Decimal("1000.10")`, never the float's
    noisy binary expansion (`Decimal(1000.10)` would carry trailing 9s).
    """
    cfg = BookkeeperConfig.from_mapping({**_VALID, "materiality_floor": 1000.10})
    assert isinstance(cfg.materiality_floor, Decimal)
    assert cfg.materiality_floor == Decimal("1000.10")


def test_materiality_floor_decimal_and_string_pass_through_exactly():
    """A Decimal or numeric string `materiality_floor` is preserved exactly."""
    as_decimal = BookkeeperConfig.from_mapping(
        {**_VALID, "materiality_floor": Decimal("250.00")}
    )
    assert as_decimal.materiality_floor == Decimal("250.00")
    as_string = BookkeeperConfig.from_mapping({**_VALID, "materiality_floor": "250.00"})
    assert isinstance(as_string.materiality_floor, Decimal)
    assert as_string.materiality_floor == Decimal("250.00")


def test_materiality_floor_coerced_on_direct_construction_too():
    """The __post_init__ guard coerces a float even on direct construction (not just from_mapping)."""
    cfg = BookkeeperConfig(
        chart_of_accounts=("5000",),
        accounting_method="cash",
        jurisdiction="XX",
        tax_regime="standard",
        accountant_format="generic-export",
        attribution_targets=("target-001",),
        books_location="generic-ledger",
        intake_channel="generic-channel",
        materiality_floor=1000.0,  # a float reaches the §5.6 Decimal comparison safely
    )
    assert isinstance(cfg.materiality_floor, Decimal)
    assert cfg.materiality_floor == Decimal("1000.0")


def test_materiality_floor_none_stays_none_inert():
    """Unset `materiality_floor` stays `None` — the inert signal flagAnomaly skips on."""
    assert BookkeeperConfig.from_mapping(_VALID).materiality_floor is None


def test_attribution_threshold_is_none_when_unset():
    """`attribution_threshold()` returns None (the inert signal) when unconfigured."""
    cfg = BookkeeperConfig.from_mapping(_VALID)
    assert cfg.attribution_threshold() is None


def test_attribution_threshold_returns_configured_value():
    cfg = BookkeeperConfig.from_mapping(
        {**_VALID, "confidence_thresholds": {ATTRIBUTION_SKILL: 0.92}}
    )
    assert cfg.attribution_threshold() == 0.92


def test_config_is_immutable():
    cfg = BookkeeperConfig.from_mapping(_VALID)
    with pytest.raises(Exception):
        cfg.books_location = "mutated"  # type: ignore[misc]


def test_collections_are_frozen():
    """Mappings are frozen on construction so the shared config can't be mutated."""
    cfg = BookkeeperConfig.from_mapping(
        {**_VALID, "confidence_thresholds": {ATTRIBUTION_SKILL: 0.9}}
    )
    with pytest.raises(TypeError):
        cfg.confidence_thresholds["attribution"] = 0.1  # type: ignore[index]
