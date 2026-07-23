"""Tests for the jr-analyst per-instance config and its fail-fast validation.

Mirrors `tests/test_config.py` (the Bookkeeper's §3 config tests), generalized to
the jr-analyst's smaller surface. The key property (AC): `from_mapping` reports
**every** missing required field at once via the same fail-fast collector, so a
misconfigured instance fails fast with one clear message. In v1 there is a single
required field (`budget_source_ref`); `align_on` is optional and defaults to the
conservative alignment grain when unset.
"""

import pytest

from jr_analyst.config import (
    DEFAULT_ALIGN_ON,
    AnalystConfig,
    AnalystConfigError,
)

# A complete, valid mapping — the one required field present.
_VALID = {"budget_source_ref": "generic-budget-source"}


def test_from_mapping_builds_a_valid_config():
    cfg = AnalystConfig.from_mapping(_VALID)
    assert isinstance(cfg, AnalystConfig)
    assert cfg.budget_source_ref == "generic-budget-source"
    # align_on unset → the conservative default grain.
    assert cfg.align_on == ("account", "period")


def test_missing_required_reports_it_by_name():
    """The core AC: a mapping missing the required field is reported by name.

    v1 has a single required field, so the fail-fast collector's "report every
    missing field at once" reduces to naming `budget_source_ref` — the same
    mechanism `BookkeeperConfig` uses, exercised on the one field the jr-analyst
    requires.
    """
    with pytest.raises(AnalystConfigError) as excinfo:
        AnalystConfig.from_mapping({"align_on": ("account",)})
    assert "budget_source_ref" in str(excinfo.value)


def test_empty_mapping_reports_the_required_field():
    with pytest.raises(AnalystConfigError) as excinfo:
        AnalystConfig.from_mapping({})
    assert "budget_source_ref" in str(excinfo.value)


def test_blank_budget_source_ref_treated_as_missing():
    """A whitespace-only `budget_source_ref` counts as unset and is reported."""
    with pytest.raises(AnalystConfigError) as excinfo:
        AnalystConfig.from_mapping({"budget_source_ref": "   "})
    assert "budget_source_ref" in str(excinfo.value)


def test_align_on_defaults_to_conservative_grain_when_unset():
    """`align_on` is optional; unset takes the conservative `(account, period)` grain."""
    cfg = AnalystConfig.from_mapping(_VALID)
    assert cfg.align_on == DEFAULT_ALIGN_ON
    assert cfg.align_on == ("account", "period")


def test_align_on_blank_falls_back_to_default():
    """A blank/empty `align_on` falls back to the default rather than aligning on nothing."""
    cfg = AnalystConfig.from_mapping({**_VALID, "align_on": ()})
    assert cfg.align_on == DEFAULT_ALIGN_ON


def test_align_on_override_is_respected_and_coerced_to_tuple():
    """A supplied `align_on` (as a list) is respected and frozen to a tuple."""
    cfg = AnalystConfig.from_mapping(
        {**_VALID, "align_on": ["account", "attribution_target_id", "period"]}
    )
    assert cfg.align_on == ("account", "attribution_target_id", "period")
    assert isinstance(cfg.align_on, tuple)


def test_align_on_frozen_to_tuple_on_direct_construction():
    """`__post_init__` freezes `align_on` to a tuple even on direct construction."""
    cfg = AnalystConfig(
        budget_source_ref="generic-budget-source",
        align_on=["account", "period"],  # a mutable list reaches the frozen config safely
    )
    assert isinstance(cfg.align_on, tuple)
    assert cfg.align_on == ("account", "period")


def test_default_align_on_is_the_shared_constant():
    """A directly-constructed config with no `align_on` uses the shared default constant."""
    cfg = AnalystConfig(budget_source_ref="generic-budget-source")
    assert cfg.align_on == DEFAULT_ALIGN_ON


def test_config_is_immutable():
    cfg = AnalystConfig.from_mapping(_VALID)
    with pytest.raises(Exception):
        cfg.budget_source_ref = "mutated"  # type: ignore[misc]


def test_align_on_is_an_immutable_tuple():
    """`align_on` is a tuple, so it exposes no in-place mutation of the shared config."""
    cfg = AnalystConfig.from_mapping(_VALID)
    assert isinstance(cfg.align_on, tuple)
    with pytest.raises(AttributeError):
        cfg.align_on.append("period")  # type: ignore[attr-defined]


def test_config_error_is_a_value_error():
    """`AnalystConfigError` is a `ValueError` (mirrors `ConfigError`), for callers that catch broadly."""
    assert issubclass(AnalystConfigError, ValueError)
