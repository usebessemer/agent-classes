"""Tests for the jr-analyst per-instance config and its fail-fast validation.

Mirrors `tests/test_config.py` (the Bookkeeper's §3 config tests), generalized to
the jr-analyst's smaller surface. The key property (AC): `from_mapping` reports
**every** missing required field at once via the same fail-fast collector, so a
misconfigured instance fails fast with one clear message. In v1 there is a single
required field (`budget_source_ref`); `align_on` is optional and defaults to the
conservative alignment grain when unset.
"""

from decimal import Decimal

import pytest

from jr_analyst.config import (
    DEFAULT_ALIGN_ON,
    _REQUIRED,
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


# --- variance_floor: the one materiality (surfacing) threshold, slice 2 ---------
#
# `variance_floor` is the single materiality floor `flag_variance` reads. It is a
# *surfacing* threshold, never an autonomy/write boundary — the analyst is
# structurally read-only. It is never required (unset → inert), and any present
# value is coerced to exact `Decimal` so it never reaches a `Decimal` delta
# comparison as a lossy `float`. These pin that shape.


def test_variance_floor_unset_is_none():
    """Unset `variance_floor` stays `None` — the inert (surface-every-variance) signal."""
    cfg = AnalystConfig.from_mapping(_VALID)
    assert cfg.variance_floor is None


def test_variance_floor_is_never_required():
    """`variance_floor` is not in `_REQUIRED`: a mapping without it still builds."""
    assert "variance_floor" not in _REQUIRED
    cfg = AnalystConfig.from_mapping(_VALID)  # no variance_floor present
    assert isinstance(cfg, AnalystConfig)
    assert cfg.variance_floor is None


def test_variance_floor_present_does_not_satisfy_required():
    """Supplying only `variance_floor` still fails fast on the missing required field.

    Guards against `variance_floor` ever being mistaken for a required field: the
    read-only analyst still cannot run without knowing where its budget lives.
    """
    with pytest.raises(AnalystConfigError) as excinfo:
        AnalystConfig.from_mapping({"variance_floor": "1000"})
    assert "budget_source_ref" in str(excinfo.value)


def test_variance_floor_string_coerced_to_exact_decimal():
    """A numeric-string floor is coerced to exact `Decimal` via `from_mapping`."""
    cfg = AnalystConfig.from_mapping({**_VALID, "variance_floor": "1000.10"})
    assert isinstance(cfg.variance_floor, Decimal)
    assert cfg.variance_floor == Decimal("1000.10")


def test_variance_floor_float_is_str_routed_not_noisy_binary():
    """A float floor routes through `str()` → exact `Decimal("1000.10")`, not the lossy binary.

    The tell that coercion went via `str()` and not `Decimal(float)`: the direct
    `Decimal(1000.10)` carries the float's noisy binary expansion (`1000.0999…`),
    so the coerced floor is *unequal* to it and equal to the exact decimal.
    """
    cfg = AnalystConfig.from_mapping({**_VALID, "variance_floor": 1000.10})
    assert isinstance(cfg.variance_floor, Decimal)
    assert cfg.variance_floor == Decimal("1000.10")
    assert cfg.variance_floor != Decimal(1000.10)


def test_variance_floor_blank_string_stays_none():
    """A blank/whitespace `variance_floor` stays `None` (never fed to `Decimal`, which would raise)."""
    cfg = AnalystConfig.from_mapping({**_VALID, "variance_floor": "   "})
    assert cfg.variance_floor is None


def test_variance_floor_coerced_on_direct_construction():
    """`__post_init__` coerces a directly-constructed float floor to exact `Decimal`."""
    cfg = AnalystConfig(
        budget_source_ref="generic-budget-source",
        variance_floor=1000.10,  # a float reaches the Decimal delta comparison safely
    )
    assert isinstance(cfg.variance_floor, Decimal)
    assert cfg.variance_floor == Decimal("1000.10")


def test_variance_floor_int_coerced_on_direct_construction():
    """A directly-constructed int floor is coerced to `Decimal` too."""
    cfg = AnalystConfig(budget_source_ref="generic-budget-source", variance_floor=1000)
    assert isinstance(cfg.variance_floor, Decimal)
    assert cfg.variance_floor == Decimal("1000")


def test_variance_floor_decimal_and_string_pass_through_exactly():
    """A `Decimal` (short-circuit) or numeric string preserves the exact value."""
    as_decimal = AnalystConfig.from_mapping(
        {**_VALID, "variance_floor": Decimal("250.00")}
    )
    assert as_decimal.variance_floor == Decimal("250.00")
    as_string = AnalystConfig.from_mapping({**_VALID, "variance_floor": "250.00"})
    assert isinstance(as_string.variance_floor, Decimal)
    assert as_string.variance_floor == Decimal("250.00")


def test_variance_floor_none_on_direct_construction():
    """A directly-constructed config with no floor leaves it `None`."""
    cfg = AnalystConfig(budget_source_ref="generic-budget-source")
    assert cfg.variance_floor is None


def test_variance_floor_is_immutable():
    """The floor is frozen with the rest of the config — a run holds it, never sets it."""
    cfg = AnalystConfig.from_mapping({**_VALID, "variance_floor": "500"})
    with pytest.raises(Exception):
        cfg.variance_floor = Decimal("1")  # type: ignore[misc]
