"""`explain_variance` skill tests — the deterministic narrative substrate under the boundary.

Each test pins one bullet of the issue's acceptance criteria:

- `explain_variance(package, config)` is **pure + sync** (drives no port — no
  source/sink/writer/port arg), returns a PROPOSED `ExplainedPackage`, and **mutates
  neither input** (a mutation-proven test deep-copies the package and compares).
- **Struck target variance:** `target_variance == sum(per-grade actual subtotals) −
  budget_referent_total` in exact `Decimal` (composed over `rollup.actual_by_certainty`,
  NOT a raw re-sum of `pair.actual.amount`); `kind` is `OVER_BUDGET`/`UNDER_BUDGET`/
  `None` by sign; `budget_referent_total` + `budget_referents_cross_target` carried
  verbatim from the roll-up.
- **Drivers:** `package.variances` grouped on `flag.pair.actual.attribution_target_id`,
  ranked `abs(delta)` desc with tie-break `(actual.source_ref, budget.source_ref,
  account)`, **full list** (no top-N), 1-based `rank`; every driver field
  (delta/kind/certainty/both source_refs) verbatim off the flag; a doctored-delta flag
  surfaces its doctored delta verbatim.
- **Footing:** `subfloor_remainder` independently equals the summed deltas of the
  target's unflagged pairs, AND the identity `target_variance == flagged_delta_total +
  subfloor_remainder` holds; an unset-floor (all-flagged) package yields
  `subfloor_remainder == Decimal("0")` per target.
- **THE LINE (deterministic-only):** no forecast/remaining/percent/EAC/run-rate/
  projected field and no narrative/prose/commentary field anywhere on the result model;
  `committed`/`anticipated` never appear on a driver; the grade rides through.
- **Two-grain fail-fast:** an orphan-target flag raises `ValueError` naming the target;
  an orphan-pair flag raises `ValueError` naming the target + both `source_ref`s.
- **Empty / order / determinism:** an empty package yields empty `explanations`
  (PROPOSED, no crash); `explanations` follow `package.rollup` (target-id) order; two
  calls over the same package produce equal `ExplainedPackage`s.

Money is `Decimal` at the model (exact currency); every struck total is asserted as an
exact `Decimal`, never a float-rounding artifact. All tests are plain `def` (sync) —
`explain_variance` drives no port, so there is nothing to await.
"""

import copy
import dataclasses
import inspect
from decimal import Decimal

from jr_analyst.model import Certainty
from jr_analyst.skills.build_report import PackageStatus, build_report
from jr_analyst.skills.explain_variance import (
    ExplainedPackage,
    TargetExplanation,
    VarianceDriver,
    explain_variance,
)
from jr_analyst.skills.flag_variance import VarianceKind, flag_variance
from tests.analyst_fakes import (
    DEFAULT_TARGET,
    SECOND_TARGET,
    a_coverage_mix_package,
    a_cross_target_budget_pair,
    a_mixed_grade_multi_target_package,
    a_multi_target_dataset,
    a_report_package,
    a_target_pair,
    a_variance_flag,
    a_variance_report,
    an_aligned_dataset,
    an_aligned_pair,
    make_config,
)

# Result-model field names that would cross THE LINE (§2, Option A) — a forecast
# figure or a generated-prose field. (Unlike the slice-3 assembler, `rank` / `driver`
# are NOT forbidden here: ranking + driver attribution is exactly the slice-4 job.)
_FORBIDDEN_FIELD_TOKENS = (
    # --- forecast (charter §2, permanently parked) ---
    "remaining",
    "percent",
    "consumed",
    "eac",
    "run_rate",
    "runrate",
    "projected",
    "projection",
    "forecast",
    "trend",
    "estimate_at",
    # --- generated prose (the LLM narrative rung is deferred) ---
    "narrative",
    "prose",
    "commentary",
)

# The result dataclasses that must never grow a forecast/prose field.
_RESULT_TYPES = (ExplainedPackage, TargetExplanation, VarianceDriver)


def _all_field_names() -> set[str]:
    """Every field name across the result model, lower-cased for token matching."""
    names: set[str] = set()
    for cls in _RESULT_TYPES:
        names |= {f.name.lower() for f in dataclasses.fields(cls)}
    return names


# --- pure + sync + no port; mutation-proven; status ---------------------------


def test_explain_variance_is_sync_and_takes_no_port_arg():
    # A skill is async iff it drives a port; `explain_variance` drives none, so it is
    # a plain `def`, and its only arguments are the composed package + config — no
    # source / sink / writer / port could smuggle a read or a mutation in.
    assert not inspect.iscoroutinefunction(explain_variance)
    signature = inspect.signature(explain_variance)
    assert list(signature.parameters) == ["package", "config"]
    # `from __future__ import annotations` makes these string annotations
    annotations = {str(p.annotation) for p in signature.parameters.values()}
    assert annotations == {"ReportPackage", "AnalystConfig"}
    for banned in ("Source", "Sink", "Writer", "Port", "Queue"):
        assert not any(banned in name for name in annotations)


def test_explain_variance_mutates_neither_input_and_writes_nothing():
    # Mutation-proven: the frozen package compares equal to a pre-call deep copy after
    # the strike, and the skill returns a value (writes nothing — it has no sink).
    package = a_mixed_grade_multi_target_package()
    before = copy.deepcopy(package)

    result = explain_variance(package, make_config())

    assert isinstance(result, ExplainedPackage)
    assert package == before  # input untouched


def test_status_is_proposed_window_and_package_carried_verbatim():
    # The read-only analyst never publishes/files/blocks: PROPOSED is reused (slice 4
    # adds no status). The package rides on verbatim (same object), window carried.
    package = a_coverage_mix_package()

    explained = explain_variance(package, make_config())

    assert explained.status is PackageStatus.PROPOSED
    assert [s.name for s in PackageStatus] == ["PROPOSED"]  # no new status added
    assert explained.window == package.window
    assert explained.package is package  # composed package carried by reference


def test_no_jr_analyst_version_added():
    """Slice 4 adds no `jr_analyst.__version__` — it shares the bookkeeper distribution."""
    import jr_analyst

    assert not hasattr(jr_analyst, "__version__")


# The full slice-4 surface #82 dual-exports: the operation + its inline result model.
_SLICE4_PUBLIC_SURFACE = (
    "explain_variance",
    "VarianceDriver",
    "TargetExplanation",
    "ExplainedPackage",
)


def test_explain_variance_surface_is_dual_exported_from_both_paths():
    # #82 AC: the whole slice-4 surface re-exports through BOTH `jr_analyst.skills` and
    # `jr_analyst` — present as an attribute, in each `__all__`, and the SAME object as
    # the skill-module definition (no shadow re-declaration). Both import paths must
    # resolve to one surface. The canonical objects are this module's own top-level
    # `from jr_analyst.skills.explain_variance import ...` names (that form always reads
    # the real submodule, unshadowed by the package re-export). Mirrors
    # `test_build_report.py::test_build_report_surface_is_dual_exported_from_both_paths`.
    import jr_analyst
    import jr_analyst.skills as skills_pkg

    canonical = globals()
    for name in _SLICE4_PUBLIC_SURFACE:
        for package in (skills_pkg, jr_analyst):
            assert hasattr(package, name), f"{name} not exported from {package.__name__}"
            assert name in package.__all__, f"{name} missing from {package.__name__}.__all__"
            assert getattr(package, name) is canonical[name], (
                f"{package.__name__}.{name} is not the skill-module object"
            )


# --- struck target variance + kind + carried referents -----------------------


def test_target_variance_is_actuals_to_date_minus_referent_total_exact_decimal():
    # Composed over the roll-up's per-grade actual subtotals (NOT a raw re-sum of
    # `pair.actual.amount`) minus the referent total carried verbatim — exact `Decimal`.
    package = a_mixed_grade_multi_target_package()

    explained = explain_variance(package, make_config())

    for explanation in explained.explanations:
        expected = (
            sum(
                (s.actual_total for s in explanation.rollup.actual_by_certainty),
                Decimal("0"),
            )
            - explanation.rollup.budget_referent_total
        )
        assert explanation.target_variance == expected
        assert isinstance(explanation.target_variance, Decimal)
    # concrete pin: DEFAULT_TARGET is (1500 + 400) − (1200 + 1200) = −500; SECOND is
    # 1200 − 1000 = +200 — the two struck variances the fixtures anchor.
    by_target = {e.attribution_target_id: e for e in explained.explanations}
    assert by_target[DEFAULT_TARGET].target_variance == Decimal("-500.00")
    assert by_target[SECOND_TARGET].target_variance == Decimal("200.00")


def test_kind_is_over_under_by_sign():
    # The over/under verdict `build_report` refused, struck here: sign classifies the
    # variance that EXISTS (never a forecast verdict).
    explained = explain_variance(a_mixed_grade_multi_target_package(), make_config())
    by_target = {e.attribution_target_id: e for e in explained.explanations}
    assert by_target[DEFAULT_TARGET].kind is VarianceKind.UNDER_BUDGET  # −500
    assert by_target[SECOND_TARGET].kind is VarianceKind.OVER_BUDGET  # +200


def test_kind_is_none_at_exactly_zero_variance():
    # A target whose actuals exactly foot to its referents has no side — `kind` is
    # `None` (there is no "on track" bucket, mirroring `flag_variance`'s no `ON_TRACK`).
    over = a_target_pair(
        DEFAULT_TARGET, tag="pos", actual_amount=Decimal("1500.00"), budget_amount=Decimal("1200.00")
    )  # +300
    under = a_target_pair(
        DEFAULT_TARGET, tag="neg", actual_amount=Decimal("900.00"), budget_amount=Decimal("1200.00")
    )  # −300
    dataset = an_aligned_dataset(aligned=(over, under))
    report = a_variance_report(pairs=(over, under))
    package = a_report_package(dataset=dataset, variance_report=report)

    (explanation,) = explain_variance(package, make_config()).explanations

    assert explanation.target_variance == Decimal("0")  # 2400 − 2400
    assert explanation.kind is None


def test_referent_total_and_cross_target_carried_verbatim_from_the_rollup():
    # Both are carried straight off the roll-up, never re-derived.
    package = a_coverage_mix_package()  # a same-target package: cross_target False
    (explanation,) = explain_variance(package, make_config()).explanations
    assert explanation.budget_referent_total == explanation.rollup.budget_referent_total
    assert isinstance(explanation.budget_referent_total, Decimal)
    assert explanation.budget_referents_cross_target is False

    # the honest converse: a cross-target budget trips the carried flag True
    cross = a_cross_target_budget_pair(actual_target=DEFAULT_TARGET, budget_target=SECOND_TARGET)
    cross_dataset = an_aligned_dataset(aligned=(cross,))
    cross_report = a_variance_report(pairs=(cross,))
    cross_package = a_report_package(dataset=cross_dataset, variance_report=cross_report)
    (cross_explanation,) = explain_variance(cross_package, make_config()).explanations
    assert cross_explanation.budget_referents_cross_target is True


def test_rollup_carried_by_reference_for_drilldown():
    # The source `TargetRollup` rides on by reference, so the per-grade breakdown and
    # the source pairs can be drilled straight into (charter §1).
    package = a_coverage_mix_package()
    (explanation,) = explain_variance(package, make_config()).explanations
    assert explanation.rollup is package.rollup[0]


# --- drivers: grouping, ranking, verbatim fields -----------------------------


def test_drivers_grouped_by_actual_target():
    # Drivers are `package.variances` matched on the flagged pair's actual target — the
    # same grouping key `build_report` rolled up on.
    explained = explain_variance(a_mixed_grade_multi_target_package(), make_config())
    by_target = {e.attribution_target_id: e for e in explained.explanations}
    assert len(by_target[DEFAULT_TARGET].drivers) == 2  # closed + open
    assert len(by_target[SECOND_TARGET].drivers) == 1
    for explanation in explained.explanations:
        assert all(
            d.flag.pair.actual.attribution_target_id == explanation.attribution_target_id
            for d in explanation.drivers
        )


def test_drivers_ranked_by_abs_delta_desc_rank_1_based():
    # Largest magnitude first, 1-based rank within the target: the open −800 outranks
    # the closed +300 (magnitude, not sign, orders the drivers).
    explained = explain_variance(a_mixed_grade_multi_target_package(), make_config())
    default = next(e for e in explained.explanations if e.attribution_target_id == DEFAULT_TARGET)

    assert [d.rank for d in default.drivers] == [1, 2]
    assert default.drivers[0].delta == Decimal("-800.00")
    assert default.drivers[0].certainty is Certainty.REALIZED_OPEN
    assert default.drivers[1].delta == Decimal("300.00")
    assert abs(default.drivers[0].delta) > abs(default.drivers[1].delta)


def test_driver_fields_are_verbatim_off_the_flag():
    # Every explanatory field is the carried flag's, verbatim (the cannot-drift
    # property precedent) — delta / kind / certainty / both source_refs.
    package = a_coverage_mix_package()
    driver = explain_variance(package, make_config()).explanations[0].drivers[0]
    flag = driver.flag

    assert driver.delta == flag.delta
    assert driver.kind is flag.kind
    assert driver.certainty is flag.certainty
    assert driver.actual_source_ref == flag.pair.actual.source_ref
    assert driver.budget_source_ref == flag.pair.budget.source_ref


def test_doctored_delta_flag_surfaces_its_doctored_delta_verbatim():
    # A flag with a deliberately-overridden delta rides through the driver verbatim —
    # `explain_variance` never re-strikes `actual − budget`, so the doctored delta
    # survives (and drives `flagged_delta_total`), mirroring build_report's non-re-strike.
    pair = an_aligned_pair()  # real delta −200.00
    doctored = a_variance_flag(pair, delta=Decimal("999.99"), kind=VarianceKind.OVER_BUDGET)
    dataset = an_aligned_dataset(aligned=(pair,))
    report = a_variance_report(flags=(doctored,))
    package = a_report_package(dataset=dataset, variance_report=report)

    (explanation,) = explain_variance(package, make_config()).explanations

    assert explanation.drivers[0].delta == Decimal("999.99")
    assert explanation.flagged_delta_total == Decimal("999.99")


def test_driver_ranking_tie_break_is_deterministic_by_source_refs():
    # Equal-magnitude flags order by (actual.source_ref, budget.source_ref, account),
    # NOT by input/dataset order: a +300 and a −300 (both abs 300) rank by source_ref.
    pair_a = a_target_pair(
        DEFAULT_TARGET, tag="a", actual_amount=Decimal("1500.00"), budget_amount=Decimal("1200.00")
    )  # +300, actual-target-001-a
    pair_b = a_target_pair(
        DEFAULT_TARGET, tag="b", actual_amount=Decimal("900.00"), budget_amount=Decimal("1200.00")
    )  # −300, actual-target-001-b
    # dataset lists b FIRST — ranking must not honor that order
    dataset = an_aligned_dataset(aligned=(pair_b, pair_a))
    report = a_variance_report(pairs=(pair_b, pair_a))
    package = a_report_package(dataset=dataset, variance_report=report)

    (explanation,) = explain_variance(package, make_config()).explanations

    assert abs(explanation.drivers[0].delta) == abs(explanation.drivers[1].delta)  # a real tie
    assert explanation.drivers[0].actual_source_ref == pair_a.actual.source_ref  # "…-a" ranks first
    assert explanation.drivers[1].actual_source_ref == pair_b.actual.source_ref


def test_drivers_are_the_full_list_no_top_n():
    # The full ranked list is emitted — no editorial top-N truncation.
    pairs = tuple(
        a_target_pair(
            DEFAULT_TARGET,
            tag=str(index),
            actual_amount=Decimal("1200.00") + Decimal(100 * (index + 1)),
            budget_amount=Decimal("1200.00"),
        )
        for index in range(5)
    )
    dataset = an_aligned_dataset(aligned=pairs)
    report = a_variance_report(pairs=pairs)
    package = a_report_package(dataset=dataset, variance_report=report)

    (explanation,) = explain_variance(package, make_config()).explanations

    assert len(explanation.drivers) == 5  # every flagged variance, none dropped
    assert [d.rank for d in explanation.drivers] == [1, 2, 3, 4, 5]
    # ranked by descending magnitude: +500, +400, +300, +200, +100
    assert [d.delta for d in explanation.drivers] == [
        Decimal("500.00"),
        Decimal("400.00"),
        Decimal("300.00"),
        Decimal("200.00"),
        Decimal("100.00"),
    ]


# --- footing: flagged / subfloor split ---------------------------------------


def test_subfloor_remainder_independently_equals_unflagged_pair_deltas():
    # `subfloor_remainder` is struck from the unflagged pairs, independently of the
    # definitional identity: the coverage-mix target carries a flagged over-floor pair
    # (+300) and an unflagged sub-floor pair (+50), so the remainder is the +50 delta.
    package = a_coverage_mix_package()
    (explanation,) = explain_variance(package, make_config()).explanations

    driver_pairs = {d.flag.pair for d in explanation.drivers}
    independent = sum(
        (
            pair.actual.amount - pair.budget.amount
            for pair in explanation.rollup.pairs
            if pair not in driver_pairs
        ),
        Decimal("0"),
    )
    assert explanation.subfloor_remainder == independent == Decimal("50.00")
    assert explanation.subfloor_remainder != Decimal("0")  # genuinely non-zero, exercised


def test_flagged_and_subfloor_foot_to_the_target_variance():
    # The footing identity holds by proof (subfloor struck independently), not by
    # construction: 350 == 300 (flagged) + 50 (subfloor).
    package = a_coverage_mix_package()
    (explanation,) = explain_variance(package, make_config()).explanations

    assert explanation.target_variance == Decimal("350.00")
    assert explanation.flagged_delta_total == Decimal("300.00")
    assert explanation.subfloor_remainder == Decimal("50.00")
    assert (
        explanation.target_variance
        == explanation.flagged_delta_total + explanation.subfloor_remainder
    )


def test_unset_floor_package_yields_zero_subfloor_remainder_per_target():
    # With the floor unset, `flag_variance` surfaces EVERY non-zero pair, so no pair is
    # left unflagged and `subfloor_remainder` is `Decimal("0")` per target.
    dataset = a_multi_target_dataset()  # two targets, both a non-zero variance
    unset = make_config(variance_floor=None)
    report = flag_variance(dataset, unset)  # unset floor → every pair flagged
    package = build_report(dataset, report, unset)

    explained = explain_variance(package, unset)

    assert explained.explanations  # non-empty, so the assertion below is not vacuous
    assert all(e.subfloor_remainder == Decimal("0") for e in explained.explanations)


# --- THE LINE (deterministic-only) -------------------------------------------


def test_no_forecast_or_prose_field_anywhere_on_the_result_model():
    # THE LINE, structural: no forecast (remaining/percent/EAC/run-rate/projected/
    # trend) and no generated-prose (narrative/prose/commentary) field bled onto the
    # slice-4 result model. (`rank` / `drivers` ARE allowed — that is the slice-4 job.)
    for name in _all_field_names():
        for token in _FORBIDDEN_FIELD_TOKENS:
            assert token not in name, f"forbidden slice-4 field token {token!r} in {name!r}"


def test_committed_and_anticipated_never_appear_on_a_driver_grade_preserved():
    # A driver's grade is the flag's (the actual's), verbatim — slice 1 yields only the
    # two realized rungs, so no driver is ever graded `committed` / `anticipated`.
    explained = explain_variance(a_mixed_grade_multi_target_package(), make_config())
    grades = {d.certainty for e in explained.explanations for d in e.drivers}
    assert grades <= {Certainty.REALIZED_CLOSED, Certainty.REALIZED_OPEN}
    assert Certainty.COMMITTED not in grades
    assert Certainty.ANTICIPATED not in grades
    # both realized grades actually rode through (the mixed-grade target carries both)
    assert grades == {Certainty.REALIZED_CLOSED, Certainty.REALIZED_OPEN}


def test_a_driver_authors_no_prose_only_the_flags_own_reason_rides_through():
    # Deterministic-only: the only human-readable text a driver exposes is the INPUT
    # flag's own traceable `reason`, carried verbatim through the referenced flag —
    # `explain_variance` writes no sentence of its own.
    package = a_coverage_mix_package()
    driver = explain_variance(package, make_config()).explanations[0].drivers[0]
    # no free-text field on the driver itself (only `rank` + the referenced `flag`)
    assert {f.name for f in dataclasses.fields(VarianceDriver)} == {"rank", "flag"}
    # the reachable text is the flag's reason (an input), not prose the skill wrote
    assert driver.flag.reason  # present, and it is the flag's own


# --- two-grain fail-fast (§3.4) ----------------------------------------------


def test_orphan_target_flag_raises_valueerror_naming_the_target():
    # A flag whose actual target owns no roll-up is a malformed package — fail fast,
    # naming the orphan target (mirroring build_report's coherence fail-fast).
    package = a_report_package()  # one target: DEFAULT_TARGET
    ghost = a_target_pair(
        "target-999", actual_amount=Decimal("1500.00"), budget_amount=Decimal("1200.00")
    )
    bad_package = dataclasses.replace(package, variances=(a_variance_flag(ghost),))

    try:
        explain_variance(bad_package, make_config())
        raise AssertionError("expected a ValueError on an orphan-target flag")
    except ValueError as exc:
        assert "target-999" in str(exc)


def test_orphan_pair_flag_raises_valueerror_naming_target_and_source_refs():
    # A flag whose target HAS a roll-up but whose pair is absent from that roll-up's
    # pairs is malformed too — fail fast, naming the target AND both `source_ref`s.
    package = a_report_package()  # DEFAULT_TARGET, roll-up pairs = the bare -200 pair
    ghost = a_target_pair(
        DEFAULT_TARGET, tag="ghost", actual_amount=Decimal("1500.00"), budget_amount=Decimal("1200.00")
    )  # same target, but distinct source_refs → not in the roll-up's pairs
    bad_package = dataclasses.replace(package, variances=(a_variance_flag(ghost),))

    try:
        explain_variance(bad_package, make_config())
        raise AssertionError("expected a ValueError on an orphan-pair flag")
    except ValueError as exc:
        message = str(exc)
        assert DEFAULT_TARGET in message
        assert ghost.actual.source_ref in message
        assert ghost.budget.source_ref in message


# --- empty / order / determinism ---------------------------------------------


def test_empty_package_yields_empty_explanations_status_proposed_no_crash():
    package = a_report_package(
        dataset=an_aligned_dataset(aligned=()), variance_report=a_variance_report(flags=())
    )

    explained = explain_variance(package, make_config())

    assert explained.explanations == ()
    assert explained.status is PackageStatus.PROPOSED
    assert explained.window == package.window


def test_explanations_follow_package_rollup_target_id_order():
    package = a_mixed_grade_multi_target_package()  # rollup order [DEFAULT, SECOND]

    explained = explain_variance(package, make_config())

    assert [e.attribution_target_id for e in explained.explanations] == [
        r.attribution_target_id for r in package.rollup
    ]
    assert [e.attribution_target_id for e in explained.explanations] == [
        DEFAULT_TARGET,
        SECOND_TARGET,
    ]


def test_two_calls_over_the_same_package_produce_equal_explained_packages():
    package = a_mixed_grade_multi_target_package()
    config = make_config()

    assert explain_variance(package, config) == explain_variance(package, config)
