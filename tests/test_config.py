"""Tests for the §3 config schema and its fail-fast validation.

Ported from instance #1's config tests, generalized to the class config surface.
The key property (AC): `from_mapping` reports **every** missing required field
at once, so a misconfigured instance fails fast with one clear message.
"""

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
