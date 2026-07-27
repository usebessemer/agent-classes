"""`build_report` skill tests — the Contract-A assembler under the read-only boundary.

Each test pins one bullet of the issue's acceptance criteria:

- `build_report(dataset, variance_report, config)` is **pure + sync** (drives no
  port — no source/sink/writer/port arg), returns a `PROPOSED` `ReportPackage`, and
  **mutates neither input** (a mutation-proven test snapshots both and compares).
- **Compose verbatim:** `pairs = dataset.aligned`, `variances =
  variance_report.flags` (the SAME objects, OVER-then-UNDER order preserved),
  `escalations = dataset.unmapped`, `window = dataset.window` — no delta re-struck,
  and `flag_variance` is **not** re-run (a report whose flags disagree with the pairs
  is carried verbatim, never re-derived).
- **Roll-up grouping** on `pair.actual.attribution_target_id` (never `None`), targets
  `sorted()`; a cross-target pair lands in the ACTUAL's bucket and trips that
  bucket's `budget_referents_cross_target`.
- **Per-certainty, no blind sum:** `actual_by_certainty` subtotals `pair.actual.amount`
  per grade in exact `Decimal`, ladder-ordered, present grades only (no fabricated
  `Decimal('0')`); a mixed closed+open target yields TWO subtotals; there is **no
  cross-grade actual total** field anywhere.
- **Partial-period honesty:** `budget_referent_total` is exact `Decimal`, carried
  SEPARATELY and NEVER differenced; no remaining/percent/EAC/run-rate/projected/
  verdict field anywhere; `basis == ACTUALS_TO_DATE`; `align_on` carried as grain
  metadata.
- **Inspectable roll-up:** each `TargetRollup` carries its source `pairs` by reference.
- **Window-coherence fail-fast:** a window mismatch raises `ValueError` naming both
  windows (never a BLOCKED package — `PackageStatus` is PROPOSED-only).
- **Foots-and-ties:** `sum(t.pair_count) == len(dataset.aligned)`; the summary counts
  match the inputs; escalations carried once.
- **THE LINE (assembly-only):** a package over flagged variances has no prose / no
  projected number; `committed` / `anticipated` are never populated.
- **Determinism:** two calls over the same inputs produce equal packages; ordering
  stable (targets by id, grades by ladder, pairs in dataset read order).
- **Empty-dataset:** empty rollup + zeroed summary, no crash.

Money is `Decimal` at the model (exact currency); every subtotal / referent total is
asserted as an exact `Decimal`, never a float-rounding artifact. All tests are plain
`def` (sync) — `build_report` drives no port, so there is nothing to await.
"""

import copy
import dataclasses
import inspect
from decimal import Decimal

from jr_analyst.model import AlignedDataset, Certainty, UnmappedKind, UnmappedLine
from jr_analyst.skills.build_report import (
    CertaintySubtotal,
    PackageStatus,
    PackageSummary,
    ReportBasis,
    ReportPackage,
    TargetRollup,
    build_report,
)
from jr_analyst.skills.flag_variance import VarianceKind, flag_variance
from tests.analyst_fakes import (
    DEFAULT_TARGET,
    SECOND_TARGET,
    a_budget_line,
    a_cross_target_budget_pair,
    a_multi_grade_pairs,
    a_multi_target_dataset,
    a_target_pair,
    a_variance_flag,
    a_variance_report,
    an_actual,
    an_aligned_dataset,
    an_aligned_pair,
    make_config,
    some_variance_flags,
)

# Result-model field names that would cross THE LINE (§2) — a narrative, a ranking,
# a driver attribution, or any forecast/remaining/percent-consumed/EAC/run-rate/
# projected/verdict figure. A substring of any of these appearing as a field on the
# result model is a slice-4 bleed into the slice-3 assembler.
_FORBIDDEN_FIELD_TOKENS = (
    "remaining",
    "percent",
    "consumed",
    "eac",
    "run_rate",
    "runrate",
    "projected",
    "projection",
    "forecast",
    "verdict",
    "over_under",
    "trend",
    "narrative",
    "prose",
    "commentary",
    "rank",
    "driver",
    "estimate_at",
)

# The result dataclasses that must never grow a slice-4 field.
_RESULT_TYPES = (ReportPackage, TargetRollup, CertaintySubtotal, PackageSummary)


def _all_field_names() -> set[str]:
    """Every field name across the result model, lower-cased for token matching."""
    names: set[str] = set()
    for cls in _RESULT_TYPES:
        names |= {f.name.lower() for f in dataclasses.fields(cls)}
    return names


def _an_unmapped_line() -> UnmappedLine:
    """One escalated line, to prove the package carries `dataset.unmapped` through once."""
    return UnmappedLine(
        line=an_actual(source_ref="escalated-001"),
        kind=UnmappedKind.UNMATCHED_ACTUAL,
        reason="no matching budget target (test fixture)",
    )


# --- the public surface ------------------------------------------------------


def test_build_report_and_result_types_import_from_skill_module():
    # #67 builds the skill module and its inline result types; the package-level
    # dual-export is slice-3 close-out (#68), so this pins the module import path.
    assert callable(build_report)
    for cls in (
        ReportPackage,
        TargetRollup,
        CertaintySubtotal,
        PackageSummary,
        ReportBasis,
        PackageStatus,
    ):
        assert dataclasses.is_dataclass(cls) or issubclass(cls, __import__("enum").Enum)


# --- pure + sync + no port; mutation-proven ---------------------------------


def test_build_report_is_sync_and_takes_no_port_arg():
    # A skill is async iff it drives a port; `build_report` drives none, so it is a
    # plain `def`, and its only arguments are the two computed inputs + config —
    # no source / sink / writer / port could smuggle a mutation in.
    assert not inspect.iscoroutinefunction(build_report)
    signature = inspect.signature(build_report)
    assert list(signature.parameters) == ["dataset", "variance_report", "config"]
    # and no argument is a source / sink / writer / port type that could read or mutate
    # (`from __future__ import annotations` makes these string annotations)
    annotations = {str(p.annotation) for p in signature.parameters.values()}
    assert annotations == {"AlignedDataset", "VarianceReport", "AnalystConfig"}
    for banned in ("Source", "Sink", "Writer", "Port", "Queue"):
        assert not any(banned in name for name in annotations)


def test_build_report_mutates_neither_input_and_writes_nothing():
    # Mutation-proven: both frozen inputs compare equal to a pre-call deep copy after
    # the assemble, and the skill returns a value (writes nothing — it has no sink).
    pairs = a_multi_grade_pairs()
    dataset = an_aligned_dataset(aligned=pairs, unmapped=(_an_unmapped_line(),))
    report = a_variance_report(pairs=pairs)
    before_dataset = copy.deepcopy(dataset)
    before_report = copy.deepcopy(report)

    result = build_report(dataset, report, make_config())

    assert isinstance(result, ReportPackage)
    assert dataset == before_dataset  # inputs untouched
    assert report == before_report


def test_package_status_is_proposed_only():
    # The read-only analyst never publishes/files/blocks: PROPOSED is the one status.
    package = build_report(an_aligned_dataset(), a_variance_report(), make_config())
    assert package.status is PackageStatus.PROPOSED
    assert [s.name for s in PackageStatus] == ["PROPOSED"]


# --- compose verbatim --------------------------------------------------------


def test_compose_carries_inputs_verbatim_by_reference():
    # `pairs` / `variances` / `escalations` / `window` are the input objects, unchanged
    # — same tuple references (identity), so nothing was re-struck or reordered.
    pairs = a_multi_grade_pairs()
    dataset = an_aligned_dataset(aligned=pairs, unmapped=(_an_unmapped_line(),))
    report = a_variance_report(pairs=pairs)

    package = build_report(dataset, report, make_config())

    assert package.pairs is dataset.aligned
    assert package.variances is report.flags
    assert package.escalations is dataset.unmapped
    assert package.window == dataset.window


def test_compose_preserves_flag_order_verbatim_never_regroups_by_kind():
    # The report's flag order is carried verbatim — `build_report` never imposes its
    # own OVER-then-UNDER grouping. Feed a deliberately non-canonical UNDER-then-OVER
    # order (which `flag_variance` would never emit) and assert it survives unchanged,
    # so the kind-order assertion bites on its own, not only via the identity backstop.
    over_pair = a_target_pair(DEFAULT_TARGET, actual_amount=Decimal("1500.00"))  # +300
    under_pair = an_aligned_pair()  # bare -200.00
    flags = some_variance_flags((under_pair, over_pair))  # UNDER first, on purpose
    assert [f.kind for f in flags] == [VarianceKind.UNDER_BUDGET, VarianceKind.OVER_BUDGET]

    dataset = an_aligned_dataset(aligned=(under_pair, over_pair))
    report = a_variance_report(flags=flags)
    package = build_report(dataset, report, make_config())

    assert package.variances is report.flags
    assert [f.kind for f in package.variances] == [
        VarianceKind.UNDER_BUDGET,
        VarianceKind.OVER_BUDGET,
    ]


def test_build_report_does_not_rerun_flag_variance():
    # The report is an INPUT, never re-derived: a dataset whose pairs *would* flag,
    # paired with a report carrying NO flags (same window), yields a package with no
    # variances. Had `build_report` re-run `flag_variance`, `variances` would be
    # non-empty (the bare pair is a real -200.00 variance).
    dataset = an_aligned_dataset()  # one flaggable -200.00 pair
    assert flag_variance(dataset, make_config()).flags  # sanity: it *would* flag
    empty_report = a_variance_report(flags=())

    package = build_report(dataset, empty_report, make_config())

    assert package.variances == ()
    assert package.variances is empty_report.flags


def test_deltas_are_not_re_struck():
    # A flag with a deliberately-overridden delta is carried verbatim — `build_report`
    # never recomputes `actual - budget`, so the doctored delta survives.
    pair = an_aligned_pair()  # real delta -200.00
    doctored = a_variance_flag(pair, delta=Decimal("999.99"), kind=VarianceKind.OVER_BUDGET)
    dataset = an_aligned_dataset(aligned=(pair,))
    report = a_variance_report(flags=(doctored,))

    package = build_report(dataset, report, make_config())

    assert package.variances[0].delta == Decimal("999.99")


# --- roll-up grouping --------------------------------------------------------


def test_rollup_groups_by_actual_target_sorted():
    # Two distinct actual targets → two roll-ups, keyed by the actual's target and
    # emitted in `sorted()` id order regardless of dataset order.
    dataset = a_multi_target_dataset(targets=(SECOND_TARGET, DEFAULT_TARGET))
    report = a_variance_report(pairs=dataset.aligned)

    package = build_report(dataset, report, make_config())

    assert [t.attribution_target_id for t in package.rollup] == sorted(
        (DEFAULT_TARGET, SECOND_TARGET)
    )


def test_cross_target_budget_lands_in_actual_bucket_and_flags():
    # Under the coarse default grain a pair aligns though its budget names a DIFFERENT
    # target: it lands in the ACTUAL's bucket, and that bucket flags the mismatch.
    pair = a_cross_target_budget_pair(actual_target=DEFAULT_TARGET, budget_target=SECOND_TARGET)
    dataset = an_aligned_dataset(aligned=(pair,))
    report = a_variance_report(pairs=(pair,))

    package = build_report(dataset, report, make_config())

    assert len(package.rollup) == 1
    bucket = package.rollup[0]
    assert bucket.attribution_target_id == DEFAULT_TARGET  # the actual's target
    assert bucket.budget_referents_cross_target is True


def test_same_target_budget_does_not_flag_cross_target():
    # The honest converse: a pair whose budget agrees on the target does not trip the
    # cross-target flag (so the flag means something when it *is* True).
    dataset = a_multi_target_dataset(targets=(DEFAULT_TARGET,))
    report = a_variance_report(pairs=dataset.aligned)

    package = build_report(dataset, report, make_config())

    assert package.rollup[0].budget_referents_cross_target is False


def test_rollup_group_key_is_never_none():
    # An actual's `attribution_target_id` is always a `str` (attributed upstream), so
    # every roll-up key is a real target id — never a `None` bucket.
    dataset = a_multi_target_dataset()
    report = a_variance_report(pairs=dataset.aligned)

    package = build_report(dataset, report, make_config())

    assert all(isinstance(t.attribution_target_id, str) for t in package.rollup)
    assert None not in {t.attribution_target_id for t in package.rollup}


# --- per-certainty, no blind sum --------------------------------------------


def test_mixed_grade_target_yields_two_ladder_ordered_subtotals():
    # A target with one closed and one open actual gets TWO subtotals — the grade
    # rides through, never blended — emitted in ladder order (closed before open).
    closed, open_pair = a_multi_grade_pairs()  # closed +300, open -800
    dataset = an_aligned_dataset(aligned=(open_pair, closed))  # deliberately open-first
    report = a_variance_report(pairs=(open_pair, closed))

    package = build_report(dataset, report, make_config())

    subtotals = package.rollup[0].actual_by_certainty
    assert [s.certainty for s in subtotals] == [
        Certainty.REALIZED_CLOSED,
        Certainty.REALIZED_OPEN,
    ]
    # exact per-grade actuals, in exact Decimal — NOT a blended cross-grade figure
    by_grade = {s.certainty: s for s in subtotals}
    assert by_grade[Certainty.REALIZED_CLOSED].actual_total == Decimal("1500.00")
    assert by_grade[Certainty.REALIZED_OPEN].actual_total == Decimal("400.00")
    assert all(isinstance(s.actual_total, Decimal) for s in subtotals)
    assert all(s.pair_count == 1 for s in subtotals)


def test_subtotals_only_cover_present_grades_no_fabricated_zero():
    # A single-grade target gets exactly ONE subtotal — no fabricated Decimal('0')
    # row for the grades that are absent.
    dataset = a_multi_target_dataset(targets=(DEFAULT_TARGET,))  # realized_closed only
    report = a_variance_report(pairs=dataset.aligned)

    package = build_report(dataset, report, make_config())

    subtotals = package.rollup[0].actual_by_certainty
    assert len(subtotals) == 1
    assert subtotals[0].certainty is Certainty.REALIZED_CLOSED


def test_no_cross_grade_actual_total_field_anywhere():
    # The per-grade subtotal is the ONLY actuals total; there is no field summing the
    # actuals across grades on the roll-up (which would blend closed and open).
    rollup_fields = {f.name for f in dataclasses.fields(TargetRollup)}
    assert "actual_total" not in rollup_fields  # that lives on CertaintySubtotal, per grade
    for name in rollup_fields:
        # no bare cross-grade "actual(s)_total" / "total_actual" on the target roll-up
        assert not (("total" in name) and ("actual" in name))


def test_subtotal_actuals_are_pure_actuals_not_mixed_with_budget():
    # A subtotal sums `actual.amount` only — the budget never leaks into it.
    pair = a_target_pair(DEFAULT_TARGET, actual_amount=Decimal("777.00"), budget_amount=Decimal("10.00"))
    dataset = an_aligned_dataset(aligned=(pair,))
    report = a_variance_report(pairs=(pair,))

    package = build_report(dataset, report, make_config())

    assert package.rollup[0].actual_by_certainty[0].actual_total == Decimal("777.00")


# --- partial-period honesty --------------------------------------------------


def test_budget_referent_total_is_raw_sum_carried_separately_never_differenced():
    # `budget_referent_total` is the exact sum of the matched referents — NOT
    # actual−budget, not budget−actual, and not folded into the actual subtotals.
    p1 = a_target_pair(DEFAULT_TARGET, tag="a", actual_amount=Decimal("500.00"), budget_amount=Decimal("300.00"))
    p2 = a_target_pair(DEFAULT_TARGET, tag="b", actual_amount=Decimal("100.00"), budget_amount=Decimal("200.00"))
    dataset = an_aligned_dataset(aligned=(p1, p2))
    report = a_variance_report(pairs=(p1, p2))

    bucket = build_report(dataset, report, make_config()).rollup[0]

    # exactly 300 + 200 — the raw referent sum, not actual−budget (100) or budget−actual (−100)
    assert bucket.budget_referent_total == Decimal("500.00")
    assert isinstance(bucket.budget_referent_total, Decimal)
    # actuals subtotalled separately (600 across the grade), carried apart from the referent
    actual_sum = sum((s.actual_total for s in bucket.actual_by_certainty), Decimal("0"))
    assert actual_sum == Decimal("600.00")


def test_basis_is_actuals_to_date():
    dataset = a_multi_target_dataset()
    report = a_variance_report(pairs=dataset.aligned)
    package = build_report(dataset, report, make_config())
    assert all(t.basis is ReportBasis.ACTUALS_TO_DATE for t in package.rollup)
    assert [b.name for b in ReportBasis] == ["ACTUALS_TO_DATE"]


def test_align_on_carried_as_grain_metadata():
    # The grain the pairs were aligned on rides onto the package, verbatim from config.
    config = make_config(align_on=("account", "attribution_target_id", "period"))
    package = build_report(an_aligned_dataset(), a_variance_report(), config)
    assert package.align_on == ("account", "attribution_target_id", "period")


def test_no_forecast_or_narrative_field_anywhere_on_the_result_model():
    # THE LINE, structural: no remaining/percent/EAC/run-rate/projected/verdict/
    # narrative/ranking/driver field bled into the slice-3 assembler's result model.
    for name in _all_field_names():
        for token in _FORBIDDEN_FIELD_TOKENS:
            assert token not in name, f"forbidden slice-4 field token {token!r} in {name!r}"


# --- inspectable roll-up -----------------------------------------------------


def test_rollup_carries_source_pairs_by_reference_for_drilldown():
    # Each roll-up carries its exact source `AlignedPair`s (by reference), so a
    # coarse-grain budget mismatch can be drilled straight into.
    closed, open_pair = a_multi_grade_pairs()
    dataset = an_aligned_dataset(aligned=(closed, open_pair))
    report = a_variance_report(pairs=(closed, open_pair))

    bucket = build_report(dataset, report, make_config()).rollup[0]

    assert bucket.pairs == (closed, open_pair)
    assert all(any(p is src for src in dataset.aligned) for p in bucket.pairs)


# --- window-coherence fail-fast ----------------------------------------------


def test_window_mismatch_raises_valueerror_naming_both_windows():
    # Divergent windows are a programming error, not a reviewable state: fail fast,
    # naming BOTH windows — never a BLOCKED package (there is no such status).
    dataset = an_aligned_dataset(window="2026-Q2")
    report = a_variance_report(window="2026-Q3")

    try:
        build_report(dataset, report, make_config())
        raise AssertionError("expected a ValueError on window mismatch")
    except ValueError as exc:
        message = str(exc)
        assert "2026-Q2" in message and "2026-Q3" in message


def test_matching_windows_do_not_raise():
    # The coherent case: same window, no raise — a PROPOSED package.
    dataset = an_aligned_dataset(window="2026-Q2")
    report = a_variance_report(window="2026-Q2")
    package = build_report(dataset, report, make_config())
    assert package.window == "2026-Q2"


# --- foots-and-ties ----------------------------------------------------------


def test_rollup_pair_counts_foot_to_the_aligned_pairs():
    # The roll-up partitions the aligned pairs: the per-target counts sum to the total.
    dataset = a_multi_target_dataset(targets=(DEFAULT_TARGET, SECOND_TARGET))
    report = a_variance_report(pairs=dataset.aligned)

    package = build_report(dataset, report, make_config())

    assert sum(t.pair_count for t in package.rollup) == len(dataset.aligned)


def test_summary_counts_match_the_inputs():
    # Every summary count is self-describing and matches the composed inputs.
    closed, open_pair = a_multi_grade_pairs()  # 2 pairs, 1 target
    escalations = (_an_unmapped_line(), _an_unmapped_line())
    dataset = an_aligned_dataset(aligned=(closed, open_pair), unmapped=escalations)
    report = a_variance_report(pairs=(closed, open_pair))

    package = build_report(dataset, report, make_config())

    assert package.summary == PackageSummary(
        aligned_pairs=2,
        escalations=2,
        flagged_variances=2,
        targets=1,
    )
    # escalations carried through once (not duplicated), the same objects
    assert package.escalations == escalations
    assert len(package.escalations) == 2


# --- THE LINE (assembly-only) ------------------------------------------------


def test_package_over_variances_has_no_prose_and_no_projected_number():
    # A package assembled over flagged variances is pure composition: no narrative
    # field authored by the skill, no projected/forecast figure. The only text on the
    # package is the input flags' own traceable `reason`s, carried verbatim.
    closed, open_pair = a_multi_grade_pairs()
    dataset = an_aligned_dataset(aligned=(closed, open_pair))
    report = a_variance_report(pairs=(closed, open_pair))

    package = build_report(dataset, report, make_config())

    # no free-text / forecast field on any result type (structural THE LINE guard)
    assert not (_all_field_names() & set(_FORBIDDEN_FIELD_TOKENS))
    # the flags carried are the SAME objects — their reasons are inputs, not prose the
    # assembler wrote
    assert package.variances is report.flags


def test_forward_looking_grades_are_never_populated():
    # Slice 1 yields only the two realized rungs; the assembler never fabricates a
    # `committed` / `anticipated` subtotal — the roll-up reflects only present grades.
    closed, open_pair = a_multi_grade_pairs()
    dataset = an_aligned_dataset(aligned=(closed, open_pair))
    report = a_variance_report(pairs=(closed, open_pair))

    package = build_report(dataset, report, make_config())

    grades = {s.certainty for t in package.rollup for s in t.actual_by_certainty}
    assert grades <= {Certainty.REALIZED_CLOSED, Certainty.REALIZED_OPEN}
    assert Certainty.COMMITTED not in grades
    assert Certainty.ANTICIPATED not in grades


# --- determinism -------------------------------------------------------------


def test_two_calls_over_same_inputs_produce_equal_packages():
    dataset = a_multi_target_dataset(targets=(SECOND_TARGET, DEFAULT_TARGET))
    report = a_variance_report(pairs=dataset.aligned)
    config = make_config()

    assert build_report(dataset, report, config) == build_report(dataset, report, config)


def test_ordering_is_stable_targets_by_id_grades_by_ladder_pairs_in_read_order():
    # Targets sorted by id; within a target, grades in ladder order; pairs in the
    # dataset's read order.
    closed, open_pair = a_multi_grade_pairs(target=SECOND_TARGET)
    first_target_pair = a_target_pair(DEFAULT_TARGET)
    aligned = (closed, first_target_pair, open_pair)  # scrambled target + grade order
    dataset = an_aligned_dataset(aligned=aligned)
    report = a_variance_report(pairs=aligned)

    package = build_report(dataset, report, make_config())

    assert [t.attribution_target_id for t in package.rollup] == [DEFAULT_TARGET, SECOND_TARGET]
    second = next(t for t in package.rollup if t.attribution_target_id == SECOND_TARGET)
    assert [s.certainty for s in second.actual_by_certainty] == [
        Certainty.REALIZED_CLOSED,
        Certainty.REALIZED_OPEN,
    ]
    # pairs preserved in the dataset's read order within the target
    assert second.pairs == (closed, open_pair)


# --- empty-dataset -----------------------------------------------------------


def test_empty_dataset_yields_empty_rollup_and_zeroed_summary():
    dataset = an_aligned_dataset(aligned=())
    report = a_variance_report(flags=())

    package = build_report(dataset, report, make_config())

    assert package.rollup == ()
    assert package.pairs == ()
    assert package.summary == PackageSummary(
        aligned_pairs=0, escalations=0, flagged_variances=0, targets=0
    )
    assert package.status is PackageStatus.PROPOSED


def test_empty_dataset_is_an_aligned_dataset_not_a_crash():
    # The empty window is a legitimate, coherent package — not an error.
    package = build_report(
        AlignedDataset(window="2026-Q2", aligned=(), unmapped=()),
        a_variance_report(window="2026-Q2", flags=()),
        make_config(),
    )
    assert isinstance(package, ReportPackage)
    assert package.window == "2026-Q2"
