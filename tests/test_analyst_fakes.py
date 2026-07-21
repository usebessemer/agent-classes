"""Tests for the jr-analyst test substrate itself (`tests/analyst_fakes.py`).

The substrate is load-bearing for every later jr-analyst skill test, so its two
promised properties are pinned here directly:

- **Aligned defaults** (AC): a bare `an_actual()` and a bare `a_budget_line()` share
  the default alignment key, on both the default `(account, period)` grain and the
  finer `(account, attribution_target_id, period)` grain — so a default actual pairs
  1:1 with a default budget with no overrides.
- **Read-only fakes that record what they read** (AC): `FakeActualsSource` /
  `FakeBudgetSource` implement the source ports, yield the seeded rows for a key,
  and record every key requested in `self.fetched` (in order) — the hook a later
  test uses to prove the skill only ever *read*.
"""

from decimal import Decimal

from jr_analyst.config import AnalystConfig, DEFAULT_ALIGN_ON
from jr_analyst.model import ActualLine, BudgetLine, Certainty
from jr_analyst.ports import ActualsSource, BudgetSource

from tests.analyst_fakes import (
    DEFAULT_PERIOD,
    FakeActualsSource,
    FakeBudgetSource,
    a_budget_line,
    an_actual,
    make_config,
)


def _key(line, grain):
    """The alignment key for `line` on `grain` — the tuple the skill matches on."""
    return tuple(getattr(line, field) for field in grain)


# --- builders: aligned defaults ----------------------------------------------


def test_default_actual_and_budget_share_the_default_alignment_key():
    """AC: bare defaults align on the conservative `(account, period)` grain."""
    assert _key(an_actual(), DEFAULT_ALIGN_ON) == _key(a_budget_line(), DEFAULT_ALIGN_ON)


def test_default_actual_and_budget_also_share_a_finer_grain():
    """The default budget carries the actual's target (not a lump), so a finer grain aligns too."""
    finer = ("account", "attribution_target_id", "period")
    assert _key(an_actual(), finer) == _key(a_budget_line(), finer)


def test_an_actual_is_a_realized_attributed_line():
    a = an_actual()
    assert isinstance(a, ActualLine)
    assert a.attribution_target_id is not None  # an actual is always attributed
    assert a.certainty is Certainty.REALIZED_CLOSED
    assert isinstance(a.amount, Decimal)
    assert a.period == DEFAULT_PERIOD


def test_a_budget_line_defaults_to_attribution_grain_and_lump_on_request():
    assert isinstance(a_budget_line(), BudgetLine)
    assert a_budget_line().attribution_target_id is not None  # attribution-grain by default
    assert a_budget_line(attribution_target_id=None).attribution_target_id is None  # lump


def test_overriding_one_side_breaks_alignment():
    """Overriding a key field on one side is how a test builds an unaligned case."""
    actual = an_actual(account="7000-travel")
    assert _key(actual, DEFAULT_ALIGN_ON) != _key(a_budget_line(), DEFAULT_ALIGN_ON)


def test_amounts_are_decimal_not_float():
    """Money is exact `Decimal` on both sides — never `float`."""
    assert isinstance(an_actual().amount, Decimal)
    assert isinstance(a_budget_line().amount, Decimal)
    assert not isinstance(an_actual().amount, float)


# --- make_config -------------------------------------------------------------


def test_make_config_builds_a_live_config():
    cfg = make_config()
    assert isinstance(cfg, AnalystConfig)
    assert cfg.budget_source_ref == "generic-budget-source"
    assert cfg.align_on == DEFAULT_ALIGN_ON


def test_make_config_passes_overrides_through_from_mapping():
    cfg = make_config(align_on=("account", "attribution_target_id", "period"))
    assert cfg.align_on == ("account", "attribution_target_id", "period")


# --- fakes: read-only, record what they read ---------------------------------


def test_fakes_implement_the_read_only_source_ports():
    assert isinstance(FakeActualsSource(), ActualsSource)
    assert isinstance(FakeBudgetSource(), BudgetSource)


async def test_fake_actuals_source_yields_seeded_lines_and_records_the_window():
    src = FakeActualsSource({DEFAULT_PERIOD: [an_actual()]})
    lines = await src.fetch_realized(DEFAULT_PERIOD)
    assert lines == [an_actual()]  # frozen dataclasses compare by value
    assert src.fetched == [DEFAULT_PERIOD]  # recorded exactly the window read


async def test_fake_budget_source_yields_seeded_lines_and_records_the_period():
    src = FakeBudgetSource({DEFAULT_PERIOD: [a_budget_line()]})
    lines = await src.fetch_budget(DEFAULT_PERIOD)
    assert lines == [a_budget_line()]
    assert src.fetched == [DEFAULT_PERIOD]


async def test_unseeded_key_yields_empty_but_still_records_the_request():
    """An unseeded window/period reads empty — but the request is still recorded."""
    actuals = FakeActualsSource()
    budgets = FakeBudgetSource()
    assert await actuals.fetch_realized("2099-Q4") == []
    assert await budgets.fetch_budget("2099-Q4") == []
    assert actuals.fetched == ["2099-Q4"]
    assert budgets.fetched == ["2099-Q4"]


async def test_fetched_records_every_request_in_order():
    src = FakeActualsSource({DEFAULT_PERIOD: [an_actual()]})
    await src.fetch_realized(DEFAULT_PERIOD)
    await src.fetch_realized("2026-Q3")
    await src.fetch_realized(DEFAULT_PERIOD)
    assert src.fetched == [DEFAULT_PERIOD, "2026-Q3", DEFAULT_PERIOD]


async def test_seeded_rows_are_copied_not_aliased():
    """Mutating the caller's seed list after construction must not change the fake."""
    seed = [an_actual()]
    src = FakeActualsSource({DEFAULT_PERIOD: seed})
    seed.append(an_actual(source_ref="actual-src-002"))
    assert await src.fetch_realized(DEFAULT_PERIOD) == [an_actual()]  # unaffected by the later append
