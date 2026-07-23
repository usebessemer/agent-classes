"""`flagVariance` skill tests — the per-pair watchdog under the read-only boundary.

Each test pins one bullet of the issue's acceptance criteria:

- `flag_variance(dataset, config)` is **pure + sync** (drives no port), reads only
  `dataset.aligned`, and returns an advisory `VarianceReport` — it mutates neither
  input, never resolves / acts on a variance, and never blocks a downstream skill.
- classification by the **signed exact-`Decimal`** delta `actual.amount -
  budget.amount`: `> 0` → `OVER_BUDGET`, `< 0` → `UNDER_BUDGET`, and there is no
  `ON_TRACK` kind (a zero delta produces no flag).
- the delta is stored signed and exact — never `abs`-stored, never `float`.
- each flag carries the pair's `certainty` **verbatim** and a §1-traceable
  `reason` embedding both `source_ref`s, both amounts, the delta, and the grade.
- the **inverted** materiality default: an unset `variance_floor` surfaces *every*
  non-zero-delta pair (not skip); a set floor surfaces only `abs(delta)` strictly
  over it (boundary-equal suppressed); a zero delta is never surfaced.
- `dataset.unmapped` is never read / re-flagged; `report.window` is the dataset's.
- deterministic order (grouped `OVER_BUDGET` then `UNDER_BUDGET`, read order
  within each kind); the §2 line held (no trend / forecast / run-rate / EAC /
  projection; `realized_open` reported at actuals-to-date with no projection;
  `budget.amount` used verbatim); the public surface is exported.

Money is `Decimal` at the model (exact currency); these fixtures pass `Decimal`
amounts so every delta / floor compare is exact, never a float-rounding artifact.
All tests are plain `def` (sync) — `flag_variance` drives no port, so there is
nothing to await.
"""

import inspect
from decimal import Decimal

from jr_analyst.model import (
    AlignedPair,
    Certainty,
    UnmappedKind,
    UnmappedLine,
)
from jr_analyst.skills.flag_variance import (
    VarianceFlag,
    VarianceKind,
    VarianceReport,
    flag_variance,
)
from tests.analyst_fakes import (
    a_budget_line,
    an_actual,
    an_aligned_dataset,
    an_aligned_pair,
    make_config,
)


def _pair(
    *,
    actual: str = "1000.00",
    budget: str = "1200.00",
    certainty: Certainty = Certainty.REALIZED_CLOSED,
    actual_ref: str = "actual-src-001",
    budget_ref: str = "budget-src-001",
) -> AlignedPair:
    """A 1:1 `AlignedPair` with the two amounts (and refs / grade) a test dictates.

    `actual` / `budget` are decimal strings (exact currency); the signed delta is
    `Decimal(actual) - Decimal(budget)`. A bare call is the ready-made -200.00
    under-budget pair (1000.00 actual vs 1200.00 budget).
    """
    return an_aligned_pair(
        actual=an_actual(amount=Decimal(actual), source_ref=actual_ref, certainty=certainty),
        budget=a_budget_line(amount=Decimal(budget), source_ref=budget_ref),
    )


def _kinds(report: VarianceReport) -> list[VarianceKind]:
    return [f.kind for f in report.flags]


# --- the public surface ------------------------------------------------------


def test_flag_variance_surface_is_exported_from_package():
    """The public surface re-exports through `jr_analyst` (the convention)."""
    import jr_analyst

    for name in ("flag_variance", "VarianceReport", "VarianceFlag", "VarianceKind"):
        assert hasattr(jr_analyst, name), f"{name} not exported from jr_analyst"
        assert name in jr_analyst.__all__, f"{name} missing from __all__"
    assert jr_analyst.flag_variance is flag_variance


def test_no_jr_analyst_version_added():
    """Slice 2 adds no `jr_analyst.__version__` — it shares the bookkeeper distribution."""
    import jr_analyst

    assert not hasattr(jr_analyst, "__version__")


# --- shape: empty, window carried, sync -------------------------------------


def test_empty_dataset_returns_empty_report_window_carried():
    """A dataset with no aligned pairs flags nothing; the window is carried verbatim."""
    report = flag_variance(an_aligned_dataset(aligned=(), window="2026-Q1"), make_config())
    assert isinstance(report, VarianceReport)
    assert report.window == "2026-Q1"
    assert report.flags == ()


def test_flag_variance_is_sync_not_a_coroutine():
    """`flag_variance` drives no port, so it is a plain sync function (async iff port)."""
    assert not inspect.iscoroutinefunction(flag_variance)
    result = flag_variance(an_aligned_dataset(), make_config())
    assert not inspect.iscoroutine(result)  # returns the report directly, not awaitable
    assert isinstance(result, VarianceReport)


# --- classification: signed delta → over / under ----------------------------


def test_over_budget_positive_signed_delta():
    """`actual > budget` → OVER_BUDGET with a positive signed delta."""
    dataset = an_aligned_dataset(aligned=(_pair(actual="1500.00", budget="1200.00"),))
    report = flag_variance(dataset, make_config())
    assert _kinds(report) == [VarianceKind.OVER_BUDGET]
    assert report.flags[0].delta == Decimal("300.00")


def test_under_budget_negative_signed_delta():
    """`actual < budget` → UNDER_BUDGET with a negative signed delta (the bare -200 pair)."""
    report = flag_variance(an_aligned_dataset(), make_config())  # bare = 1000 vs 1200
    assert _kinds(report) == [VarianceKind.UNDER_BUDGET]
    flag = report.flags[0]
    assert isinstance(flag, VarianceFlag)
    assert flag.delta == Decimal("-200.00")


def test_variance_kind_has_no_on_track_member():
    """A flag *is* a surfaced material variance — there is no `ON_TRACK` kind."""
    assert {k.value for k in VarianceKind} == {"over_budget", "under_budget"}
    assert not hasattr(VarianceKind, "ON_TRACK")


def test_delta_is_signed_exact_decimal_never_abs_never_float():
    """The stored delta is the signed, exact `Decimal` difference — not `abs`, not `float`."""
    # A cents delta a float would render noisily (0.1 is inexact in binary).
    over = flag_variance(
        an_aligned_dataset(aligned=(_pair(actual="1000.10", budget="1000.00"),)), make_config()
    ).flags[0]
    assert isinstance(over.delta, Decimal)
    assert over.delta == Decimal("0.10")  # exact, not 0.1000000000000000055...

    # Under budget keeps the sign — never abs-stored.
    under = flag_variance(
        an_aligned_dataset(aligned=(_pair(actual="1000.00", budget="1000.10"),)), make_config()
    ).flags[0]
    assert under.delta == Decimal("-0.10")
    assert under.delta < 0  # sign preserved, not absolute value


# --- traceability + grade ----------------------------------------------------


def test_certainty_carried_verbatim_from_pair():
    """The flag carries the pair's grade verbatim — a `realized_open` variance stays open."""
    dataset = an_aligned_dataset(
        aligned=(_pair(actual="1500.00", budget="1200.00", certainty=Certainty.REALIZED_OPEN),)
    )
    flag = flag_variance(dataset, make_config()).flags[0]
    assert flag.certainty is Certainty.REALIZED_OPEN


def test_reason_is_traceable_embeds_refs_amounts_delta_grade():
    """§1: the reason embeds both source_refs, both amounts, the delta, and the grade."""
    dataset = an_aligned_dataset(
        aligned=(
            _pair(
                actual="1500.00",
                budget="1200.00",
                actual_ref="actual-src-XYZ",
                budget_ref="budget-src-XYZ",
                certainty=Certainty.REALIZED_OPEN,
            ),
        )
    )
    reason = flag_variance(dataset, make_config()).flags[0].reason
    assert "actual-src-XYZ" in reason and "budget-src-XYZ" in reason  # both source_refs
    assert "1500.00" in reason and "1200.00" in reason  # both amounts
    assert "300.00" in reason  # the delta
    assert Certainty.REALIZED_OPEN.value in reason  # grade.value verbatim


# --- materiality floor: the INVERTED default --------------------------------


def test_unset_floor_surfaces_every_nonzero_pair_inverted_default():
    """Inverted: an unset `variance_floor` surfaces EVERY non-zero-delta pair (not skip).

    Distinct from `flag_anomaly`'s over-materiality check, where an unset floor
    *skips*. Here (charter §3/§5.4) inert means *surface everything, suppress none*.
    """
    dataset = an_aligned_dataset(
        aligned=(
            _pair(actual="1001.00", budget="1000.00"),  # +1.00 — tiny, still surfaced
            _pair(actual="1000.00", budget="1000.01"),  # -0.01 — tinier, still surfaced
            _pair(actual="9000.00", budget="1000.00"),  # +8000 — surfaced
        )
    )
    report = flag_variance(dataset, make_config(variance_floor=None))
    assert len(report.flags) == 3  # every non-zero delta, none suppressed


def test_set_floor_surfaces_only_strictly_above():
    """With the floor set, only `abs(delta)` strictly over it is surfaced."""
    dataset = an_aligned_dataset(
        aligned=(
            _pair(actual="1100.00", budget="1000.00"),  # +100 — below the 250 floor, suppressed
            _pair(actual="1400.00", budget="1000.00"),  # +400 — over the floor, surfaced
            _pair(actual="600.00", budget="1000.00"),  # -400 — abs over the floor, surfaced
        )
    )
    report = flag_variance(dataset, make_config(variance_floor=Decimal("250")))
    deltas = sorted(f.delta for f in report.flags)
    assert deltas == [Decimal("-400.00"), Decimal("400.00")]  # only the two over the floor


def test_zero_delta_never_surfaced_even_with_floor_unset():
    """A zero delta is never surfaced — no variance to flag (even under the inverted default)."""
    dataset = an_aligned_dataset(
        aligned=(
            _pair(actual="1000.00", budget="1000.00"),  # exactly on budget
            _pair(actual="1500.00", budget="1200.00"),  # a real +300 variance
        )
    )
    report = flag_variance(dataset, make_config(variance_floor=None))
    assert _kinds(report) == [VarianceKind.OVER_BUDGET]  # only the non-zero pair
    assert report.flags[0].delta == Decimal("300.00")


def test_boundary_equal_delta_suppressed_strict_greater_than():
    """A delta exactly equal to the floor is suppressed — the comparison is strict `>`."""
    dataset = an_aligned_dataset(aligned=(_pair(actual="1250.00", budget="1000.00"),))  # +250
    report = flag_variance(dataset, make_config(variance_floor=Decimal("250")))
    assert report.flags == ()  # abs(delta) == floor → not strictly over → suppressed


def test_non_finite_floor_surfaces_every_variance_fail_safe():
    """Fail-safe: a non-finite floor surfaces a real variance — never suppresses, never crashes.

    The config layer coerces the floor with `Decimal(str(...))` and passes
    `Infinity` / `NaN` through verbatim. Left unguarded, `abs(delta) <= Infinity`
    is always `True` (every variance silently suppressed — a watchdog gone dark)
    and `abs(delta) <= NaN` raises `decimal.InvalidOperation`. The `is_finite()`
    guard treats a non-finite floor as inert (the inverted default): surface
    everything. A real +8000 variance must still be surfaced under either.
    """
    dataset = an_aligned_dataset(aligned=(_pair(actual="9000.00", budget="1000.00"),))  # +8000
    for floor in (Decimal("Infinity"), Decimal("NaN")):
        report = flag_variance(dataset, make_config(variance_floor=floor))
        assert _kinds(report) == [VarianceKind.OVER_BUDGET], f"floor={floor} suppressed a variance"
        assert report.flags[0].delta == Decimal("8000.00")


# --- scope: only `aligned`, window from dataset -----------------------------


def test_unmapped_is_not_read_or_reflagged():
    """The escalated `dataset.unmapped` remainder is never read — assembling it is slice 3."""
    escalation = UnmappedLine(
        line=an_actual(amount=Decimal("777.00"), source_ref="unmapped-actual"),
        kind=UnmappedKind.UNMATCHED_ACTUAL,
        reason="an escalated actual with no budget — must not be variance-flagged",
    )
    dataset = an_aligned_dataset(
        aligned=(_pair(actual="1500.00", budget="1200.00"),),  # one real +300 variance
        unmapped=(escalation,),
    )
    report = flag_variance(dataset, make_config())
    assert len(report.flags) == 1  # only the aligned pair, never the unmapped line
    assert all("unmapped-actual" not in f.reason for f in report.flags)


def test_report_window_is_the_dataset_window():
    """`report.window` carries `dataset.window` verbatim."""
    report = flag_variance(an_aligned_dataset(window="2026-Q4-review"), make_config())
    assert report.window == "2026-Q4-review"


# --- deterministic ordering --------------------------------------------------


def test_flags_grouped_over_then_under_stable_read_order():
    """Flags group by kind (OVER_BUDGET then UNDER_BUDGET), read order within each kind."""
    dataset = an_aligned_dataset(
        aligned=(
            _pair(actual="600.00", budget="1000.00", actual_ref="under-A"),  # -400 under
            _pair(actual="1400.00", budget="1000.00", actual_ref="over-A"),  # +400 over
            _pair(actual="500.00", budget="1000.00", actual_ref="under-B"),  # -500 under
            _pair(actual="1500.00", budget="1000.00", actual_ref="over-B"),  # +500 over
        )
    )
    report = flag_variance(dataset, make_config())
    assert _kinds(report) == [
        VarianceKind.OVER_BUDGET,
        VarianceKind.OVER_BUDGET,
        VarianceKind.UNDER_BUDGET,
        VarianceKind.UNDER_BUDGET,
    ]
    # within each kind, dataset.aligned read order is preserved
    refs = [f.pair.actual.source_ref for f in report.flags]
    assert refs == ["over-A", "over-B", "under-A", "under-B"]


# --- §5: advisory — reads only, mutates nothing, never blocks ---------------


def test_mutates_neither_input_and_returns_report():
    """§5: the skill returns a report and mutates neither the dataset nor the config.

    Both inputs are frozen dataclasses, so this pins the contract: the same
    `aligned` tuple identity survives the call and a re-run yields an equal report.
    """
    dataset = an_aligned_dataset(aligned=(_pair(actual="1500.00", budget="1200.00"),))
    config = make_config(variance_floor=Decimal("100"))
    before_aligned = dataset.aligned
    before_floor = config.variance_floor

    report = flag_variance(dataset, config)

    assert isinstance(report, VarianceReport)  # returned, not raised / not a gate
    assert dataset.aligned is before_aligned  # input untouched
    assert config.variance_floor == before_floor
    # deterministic: a second run over the same inputs is equal
    assert flag_variance(dataset, config) == report


# --- §2 line: non-forecasting (forward-looking, not a forecast) -------------


def test_realized_open_reported_at_actuals_to_date_with_no_projection():
    """§2: an in-flight `realized_open` pair is reported at actuals-to-date, no projection.

    The delta is exactly `actual.amount - budget.amount` — the to-date variance —
    with no projected final / run-rated number grafted on, and `budget.amount` is
    used verbatim as the subtrahend (never prorated / percent-complete-adjusted).
    """
    dataset = an_aligned_dataset(
        aligned=(_pair(actual="400.00", budget="1000.00", certainty=Certainty.REALIZED_OPEN),)
    )
    flag = flag_variance(dataset, make_config()).flags[0]
    assert flag.certainty is Certainty.REALIZED_OPEN
    assert flag.delta == Decimal("-600.00")  # 400 - 1000 exactly; no projection to a final figure
    assert flag.kind is VarianceKind.UNDER_BUDGET


def test_docstring_documents_non_forecasting_scope():
    """The module docstring disclaims the §2 forecasting overlay (THE LINE block).

    `inspect.getmodule` resolves the defining module reliably — importing the
    submodule by name would bind the *function*, not the module docstring.
    """
    mod = inspect.getmodule(flag_variance)
    raw = mod.__doc__ or ""
    doc = raw.lower()
    assert "§2" in raw
    for excluded in ("trend", "forecast", "run-rate", "eac", "projection"):
        assert excluded in doc, f"docstring should disclaim {excluded!r}"
