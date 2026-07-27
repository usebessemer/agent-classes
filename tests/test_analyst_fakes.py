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

import pytest

from jr_analyst.config import AnalystConfig, DEFAULT_ALIGN_ON
from jr_analyst.model import ActualLine, AlignedDataset, BudgetLine, Certainty
from jr_analyst.ports import ActualsSource, BudgetSource
from jr_analyst.skills.flag_variance import VarianceFlag, VarianceKind, VarianceReport

from tests.analyst_fakes import (
    DEFAULT_PERIOD,
    DEFAULT_TARGET,
    SECOND_TARGET,
    FakeActualsSource,
    FakeBudgetSource,
    a_budget_line,
    a_cross_target_budget_pair,
    a_multi_grade_pairs,
    a_multi_target_dataset,
    a_target_pair,
    a_variance_flag,
    a_variance_report,
    an_actual,
    an_aligned_pair,
    make_config,
    some_variance_flags,
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


# --- slice-3 builders: multi-target / multi-grade / cross-target -------------
# Each test pins one AC bullet — the roll-up substrate `build_report` (slice 3)
# consumes. The builders layer on the slice-1/-2 defaults and never touch the
# bare -200.00 builders (proven green by the tests above).


def test_multi_target_dataset_spans_at_least_two_distinct_targets():
    """AC: the dataset spans ≥2 distinct `actual.attribution_target_id`, each with its own ref."""
    ds = a_multi_target_dataset()
    assert isinstance(ds, AlignedDataset)
    targets = {p.actual.attribution_target_id for p in ds.aligned}
    assert len(targets) >= 2 and targets == {DEFAULT_TARGET, SECOND_TARGET}
    refs = {p.actual.source_ref for p in ds.aligned}
    assert len(refs) == len(ds.aligned)  # a distinct source_ref per target (override per target)


def test_multi_target_pairs_each_align_on_the_default_grain():
    """Every multi-target pair is a genuine 1:1 alignment on the coarse default grain."""
    for p in a_multi_target_dataset().aligned:
        assert _key(p.actual, DEFAULT_ALIGN_ON) == _key(p.budget, DEFAULT_ALIGN_ON)


def test_multi_target_per_target_magnitudes_are_distinct():
    """Distinct over-budget magnitudes per target, so a per-target roll-up sum is not merged."""
    deltas = [
        p.actual.amount - p.budget.amount for p in a_multi_target_dataset().aligned
    ]
    assert deltas == [Decimal("100.00"), Decimal("200.00")]  # +100*(i+1), distinct per target


def test_multi_grade_pairs_carry_both_realized_grades_under_one_target():
    """AC: two pairs share a target but carry `REALIZED_CLOSED` and `REALIZED_OPEN`."""
    closed, open_ = a_multi_grade_pairs()
    assert closed.actual.attribution_target_id == open_.actual.attribution_target_id
    assert closed.certainty is Certainty.REALIZED_CLOSED
    assert open_.certainty is Certainty.REALIZED_OPEN
    assert closed.actual.source_ref != open_.actual.source_ref  # distinguishable rows


def test_multi_grade_pairs_are_over_then_under_for_a_no_blind_sum_test():
    """Closed pair is +300 over, open pair is -800 under — a naive cross-grade sum (-500) is visibly wrong."""
    closed, open_ = a_multi_grade_pairs()
    assert closed.actual.amount - closed.budget.amount == Decimal("300.00")
    assert open_.actual.amount - open_.budget.amount == Decimal("-800.00")


def test_cross_target_budget_pair_differs_in_target_but_aligns_on_coarse_grain():
    """AC: budget's target differs from the actual's, yet the pair aligns on `(account, period)`."""
    p = a_cross_target_budget_pair()
    assert p.actual.attribution_target_id != p.budget.attribution_target_id
    assert _key(p.actual, DEFAULT_ALIGN_ON) == _key(p.budget, DEFAULT_ALIGN_ON)  # still aligns
    # the trap: on a finer target-aware grain this same pair would NOT align
    finer = ("account", "attribution_target_id", "period")
    assert _key(p.actual, finer) != _key(p.budget, finer)


def test_cross_target_budget_pair_requires_distinct_targets():
    """A cross-target pair with equal targets is a builder misuse — it asserts loudly."""
    with pytest.raises(AssertionError):
        a_cross_target_budget_pair(actual_target=DEFAULT_TARGET, budget_target=DEFAULT_TARGET)


# --- slice-3 builders: a coherent VarianceReport without running flag_variance


def test_a_variance_flag_is_coherent_with_its_pair():
    """A flag derives kind / delta / certainty from its pair; the reason is §1-traceable."""
    pair = a_target_pair(
        "target-007", actual_amount=Decimal("1500.00"), budget_amount=Decimal("1200.00")
    )
    flag = a_variance_flag(pair)
    assert isinstance(flag, VarianceFlag)
    assert flag.delta == Decimal("300.00")
    assert flag.kind is VarianceKind.OVER_BUDGET
    assert flag.certainty is pair.certainty  # grade verbatim from the pair
    assert flag.pair is pair
    # traceable reason: both source_refs, both amounts, the delta, the grade
    assert pair.actual.source_ref in flag.reason and pair.budget.source_ref in flag.reason
    assert "1500.00" in flag.reason and "1200.00" in flag.reason and "300.00" in flag.reason
    assert pair.certainty.value in flag.reason


def test_a_variance_flag_delta_is_signed_exact_decimal_never_abs_never_float():
    """Under-budget keeps the sign; the delta is exact `Decimal`, never `abs`, never `float`."""
    pair = a_target_pair(
        "target-008", actual_amount=Decimal("400.00"), budget_amount=Decimal("1200.00")
    )
    flag = a_variance_flag(pair)
    assert flag.delta == Decimal("-800.00")
    assert flag.kind is VarianceKind.UNDER_BUDGET
    assert isinstance(flag.delta, Decimal) and not isinstance(flag.delta, float)


def test_a_variance_flag_rejects_a_zero_delta_pair_without_explicit_kind():
    """A zero-delta pair has no variance — the builder refuses to guess a side."""
    on_budget = a_target_pair(
        "target-009", actual_amount=Decimal("1000.00"), budget_amount=Decimal("1000.00")
    )
    with pytest.raises(ValueError):
        a_variance_flag(on_budget)


def test_some_variance_flags_one_per_pair_in_given_order():
    """One coherent flag per pair, in the given order — no regrouping, no floor."""
    closed, open_ = a_multi_grade_pairs()
    flags = some_variance_flags((closed, open_))
    assert [f.pair for f in flags] == [closed, open_]  # order preserved as given
    assert [f.certainty for f in flags] == [
        Certainty.REALIZED_CLOSED,
        Certainty.REALIZED_OPEN,
    ]
    assert [f.kind for f in flags] == [VarianceKind.OVER_BUDGET, VarianceKind.UNDER_BUDGET]


def test_a_variance_report_wraps_pairs_with_window_spanning_targets():
    """AC: a coherent `VarianceReport` from pairs, window carried, spanning ≥2 targets — no skill run."""
    ds = a_multi_target_dataset(window="2026-Q2-rollup")
    report = a_variance_report(pairs=ds.aligned, window="2026-Q2-rollup")
    assert isinstance(report, VarianceReport)
    assert report.window == "2026-Q2-rollup"
    assert len(report.flags) == len(ds.aligned)
    assert len({f.pair.actual.attribution_target_id for f in report.flags}) >= 2


def test_a_variance_report_bare_wraps_the_default_minus_200_pair():
    """A bare call wraps the single -200.00 pair as one under-budget flag over the default window."""
    report = a_variance_report()
    assert report.window == DEFAULT_PERIOD
    assert len(report.flags) == 1
    assert report.flags[0].delta == Decimal("-200.00")
    assert report.flags[0].kind is VarianceKind.UNDER_BUDGET


def test_a_variance_report_accepts_hand_built_flags():
    """Passing `flags=` wraps them verbatim (order and all), for a fully bespoke report."""
    pair = an_aligned_pair()
    flags = some_variance_flags((pair,))
    report = a_variance_report(flags=flags, window="2026-Q3")
    assert report.window == "2026-Q3"
    assert report.flags == flags


def test_a_variance_report_rejects_both_pairs_and_flags():
    """Supplying both `pairs` and `flags` is a caller error — the builder refuses to guess."""
    pair = an_aligned_pair()
    with pytest.raises(ValueError):
        a_variance_report(pairs=(pair,), flags=some_variance_flags((pair,)))


def test_variance_report_grade_separation_substrate():
    """A report over one target at two grades — the input a no-blind-sum roll-up test needs."""
    closed, open_ = a_multi_grade_pairs(target="target-010")
    report = a_variance_report(pairs=(closed, open_))
    assert {f.certainty for f in report.flags} == {
        Certainty.REALIZED_CLOSED,
        Certainty.REALIZED_OPEN,
    }
    # one target, two grades: a naive cross-grade sum would be +300 + (-800) = -500,
    # but each grade rides through so a roll-up can keep them separate.
    assert {f.pair.actual.attribution_target_id for f in report.flags} == {"target-010"}
