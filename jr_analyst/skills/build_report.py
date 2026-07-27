"""`build_report` — the Contract-A assembler: compose one PROPOSED FP&A package.

The slice-3 jr-analyst skill, downstream of `ingest_and_align` (slice 1) and
`flag_variance` (slice 2). Where those skills *align* a window's certainty-graded
actuals to budget and *flag* the material variances, this one *composes* their two
already-computed outputs — an `AlignedDataset` and a `VarianceReport` — into one
`ReportPackage`: the FP&A deliverable, plus a **per-target roll-up** that groups the
aligned pairs by the actual's attribution target and subtotals each target's actuals
per certainty grade. It is the analyst's counterpart to the Bookkeeper's
`generate_accountant_package`: the composition step that turns the pipeline's outputs
into the deliverable — and, like it, it **assembles, it does not act**.

It mirrors `bookkeeper.generate_accountant_package` exactly where the pattern
carries over — **assemble-not-write**, **PROPOSED-only** (no PUBLISHED/FILED),
**compose-verbatim** (every figure comes from the inputs, none re-struck),
**deterministic order**, **mutation-proven** — and drops the one piece that does
not: that skill *refuses* to package a period whose close is `BLOCKED`, but the
analyst runs the **live open period** (there is no close to gate on), so there is
**no BLOCKED status and no refuse gate** here. The package is always `PROPOSED`; the
one hard stop is a window-coherence mismatch between the two inputs, which is a
programming error (fail-fast `ValueError`), not a reviewable package state.

The read-only §5-style boundary, preserved exactly (charter §1):

- **Assemble + propose, never write.** `build_report` *returns* a `ReportPackage`.
  It takes an already-computed `AlignedDataset`, an already-computed
  `VarianceReport`, and a config — **no source, no sink, no writer, no port** in its
  signature — so it structurally *cannot* read a system, mutate, or publish. It is
  pure and **sync**: a skill is async iff it drives a port, and this one drives none.
  It mutates neither input, calls `flag_variance` for nothing, re-strikes no delta,
  and blocks no downstream skill — the package is a note for a human, not a gate.
- **Every figure is traceable + graded.** The composed `pairs` / `variances` /
  `escalations` are the input objects **verbatim** (same objects, same order), so
  each still links back to its source line via its `source_ref`. The roll-up
  subtotals carry the ladder grade **per certainty** rather than flattening a target
  to a single ungraded total — a `realized_open` actual is subtotalled graded open,
  never blended into a closed figure. Traceability, not "never wrong", is the trust
  wedge (charter §1): each `TargetRollup` also carries its source `pairs` by
  reference, so a coarse-grain budget mismatch can be drilled straight into.

🚧 **THE LINE — §2, assembly-only (the boundary most likely to bloat).**
`build_report` **composes**; it does not narrate, rank, attribute drivers, or
forecast. It is the slice-3 (assembly) side of the slice-3-vs-slice-4 line: turning
the package into a *narrative* — "marketing overran because…", "the top three
variances are…", a projected final, an EAC, a run-rate, a percent-consumed, a
remaining-budget, an over/under **verdict** on a target — is the parked slice-4
narrative rung, excluded here by charter §2. Concretely, the line shows up as four
pins:

1. **No prose, no ranking, no driver attribution.** A package assembled over a set
   of flagged variances contains **no narrative text** and **no ranked "top‑N"** — it
   carries the flags in their input order and groups the pairs by target, nothing more.
2. **Partial-period honest — actuals only, never differenced.** A target's actuals
   are subtotalled **per certainty grade** as exact `Decimal`s; the matched budget
   referents are summed into `budget_referent_total`, carried **separately** and
   **never subtracted** from the actuals. There is deliberately **no** remaining,
   percent-consumed, EAC, run-rate, projected-final, or over/under-verdict field
   **anywhere** on the roll-up, and **no cross-grade actual total** that would blend a
   settled closed figure with an in-flight open one. `basis` is `ACTUALS_TO_DATE`.
3. **Honest budget-referent labeling.** `budget_referent_total` is *"the sum of the
   matched budget referents at grain `align_on`"* — **not** the target's authored
   budget. Under the conservative coarse grain (`align_on = ('account', 'period')`) a
   pair can align even though its budget names a *different* target; such a pair lands
   in the **actual's** bucket and trips that bucket's `budget_referents_cross_target`
   flag, so the coarse-grain referent is never silently passed off as the target's
   own plan.
4. **The forward-looking rungs are never fabricated.** The roll-up reflects only the
   grades the pairs actually carry (slice 1 yields the two realized rungs); it never
   synthesises a `committed` / `anticipated` subtotal — those are the parked
   forward-looking rungs a later slice's line types carry, not something assembled here.

`build_report` takes the `VarianceReport` as an **input** — it does **not** re-run
`flag_variance` (composition decision, charter): the flags are carried verbatim, the
delta is never re-struck, and the two inputs must describe the same analysis
`window` (a mismatch is a fail-fast `ValueError` naming both windows, not a package).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from jr_analyst.config import AnalystConfig
from jr_analyst.model import AlignedDataset, AlignedPair, Certainty, UnmappedLine
from jr_analyst.skills.flag_variance import VarianceFlag, VarianceReport

# The certainty ladder's definition order (most- to least-certain), captured once so
# a target's per-grade subtotals are emitted ladder-ordered and deterministically.
# `Certainty` is defined most-certain first (realized_closed → anticipated), so
# enumeration order *is* ladder order — no separate ordering table to drift.
_LADDER_ORDER: dict[Certainty, int] = {grade: index for index, grade in enumerate(Certainty)}


# --- The result model (proposed, per-target, partial-period honest) ----------


class ReportBasis(str, Enum):
    """The basis a roll-up's figures are stated on — actuals-to-date, never projected.

    A `str` enum (like `Certainty` / `PackageStatus` / the Bookkeeper's `RunOutcome`)
    so the basis serializes to a stable, readable tag for the review surface. There
    is **deliberately one member**: the analyst runs the live open period and reports
    what has been incurred *to date*, never a forecast-to-completion. It is the
    partial-period-honesty pin made explicit on the package (charter §2): a reviewer
    reads the basis and knows the actuals are in-flight, not a projected final.
    """

    #: Figures are the actuals incurred **to date** in the (possibly still-open)
    #: window — never prorated, run-rated, or projected to a final number.
    ACTUALS_TO_DATE = "actuals_to_date"


class PackageStatus(str, Enum):
    """Whether the FP&A package was assembled — PROPOSED only, never published.

    A `str` enum (like `ReportBasis` / the Bookkeeper's `PackageStatus`) so the
    status serializes to a stable, readable tag. There is **deliberately one
    member**: `build_report` never publishes (the read-only §5 boundary — there is
    no sink), so the most it produces is a `PROPOSED` package for a human's review.
    Unlike the Bookkeeper's package status there is **no `BLOCKED`**: that skill
    refuses to package a period whose *close* is not ready, but the analyst runs the
    live open period — there is no close to gate on, so no blocked package state.
    A window-coherence mismatch between the inputs is a fail-fast `ValueError`
    (a programming error), never a `BLOCKED` package.
    """

    #: The package is assembled and proposed for a human's planning review. The only
    #: status: the read-only analyst never publishes/files, and never blocks.
    PROPOSED = "proposed"


@dataclass(frozen=True)
class CertaintySubtotal:
    """One certainty grade's actuals subtotal within a target's roll-up.

    The per-grade breakdown that keeps the roll-up honest: `actual_total` is the
    exact-`Decimal` sum of `pair.actual.amount` over the pairs in this target that
    carry `certainty`, and `pair_count` is how many. A target with both settled
    (`realized_closed`) and in-flight (`realized_open`) actuals yields **two**
    subtotals, never one blended figure — so a reviewer never mistakes an in-flight
    open cost for a settled closed one. Only grades **actually present** get a
    subtotal (no fabricated `Decimal('0')` rows), and the subtotals are emitted in
    ladder order (most- to least-certain).
    """

    certainty: Certainty
    actual_total: Decimal
    pair_count: int


@dataclass(frozen=True)
class TargetRollup:
    """The roll-up of one attribution target's aligned pairs — actuals, per grade.

    Groups every aligned pair whose **actual** is attributed to
    `attribution_target_id` (never `None`), and reports:

    - `actual_by_certainty` — the per-grade actuals subtotals (ladder-ordered, only
      present grades). There is **deliberately no cross-grade total** field: blending
      a settled closed figure with an in-flight open one into a single number is the
      dishonesty this breakdown exists to prevent.
    - `budget_referent_total` — the exact-`Decimal` sum of the pairs' **matched
      budget referents** at grain `align_on`. Carried **separately** from the actual
      subtotals and **never differenced** against them: partial-period honesty means
      the package shows what was spent and what the referents summed to, and lets a
      human judge, rather than striking a remaining/over-under number the open period
      cannot support. It is *the referent sum at this grain*, **not** the target's
      authored budget.
    - `budget_referents_cross_target` — `True` when any pair's budget referent does
      **not** agree with this bucket's target: under `align_on = ('account', 'period')`
      a pair can align though its budget names a *different* target. (`ingest_and_align`
      escalates a lump `None`-target budget rather than aligning it, so a `None`
      referent does not arise from the pipeline; the comparison still handles it
      defensively for a hand-built dataset.) The honest flag that `budget_referent_total`
      mixes in a referent this target does not exclusively own.
    - `basis` — `ACTUALS_TO_DATE` (the partial-period-honesty pin).
    - `pairs` — the target's source `AlignedPair`s **by reference**, so the coarse-
      grain budget mismatch can be drilled straight into (charter §1: traceable).

    Assembly-only (§2): there is no narrative, no ranking, no driver attribution, and
    no forecast/remaining/percent-consumed/EAC field anywhere on the roll-up.
    """

    attribution_target_id: str
    actual_by_certainty: tuple[CertaintySubtotal, ...]
    budget_referent_total: Decimal
    budget_referents_cross_target: bool
    basis: ReportBasis
    pairs: tuple[AlignedPair, ...]

    @property
    def pair_count(self) -> int:
        """How many aligned pairs this target rolls up (the grouped `pairs`, by count)."""
        return len(self.pairs)


@dataclass(frozen=True)
class PackageSummary:
    """The package's self-describing counts (Contract-A's summary).

    The four counts a reviewer reads without reaching back into the inputs:
    `aligned_pairs` is every pair the dataset aligned; `escalations` the unmapped
    remainder carried through; `flagged_variances` the variances the report
    surfaced; `targets` how many distinct attribution targets the roll-up spans. By
    construction `aligned_pairs == sum(t.pair_count for t in rollup)` (the roll-up
    partitions the aligned pairs) and `targets == len(rollup)`.
    """

    aligned_pairs: int
    escalations: int
    flagged_variances: int
    targets: int


@dataclass(frozen=True)
class ReportPackage:
    """The FP&A Contract-A deliverable — **proposed, never published** (§5).

    What `build_report` returns. Composes slice 1's `AlignedDataset` and slice 2's
    `VarianceReport` verbatim (`pairs` / `variances` / `escalations` are the input
    objects, in their input order — nothing re-struck), and adds the per-target
    `rollup` and the `summary`. `status` is always `PROPOSED`: the read-only analyst
    never publishes, files, or blocks. `window` is the dataset's, verbatim (and must
    equal the variance report's — `build_report` fails fast otherwise); `align_on` is
    the grain the pairs were aligned on, carried as metadata so a reviewer reads the
    roll-up's grouping grain without re-deriving it. Ordering is deterministic:
    targets by id, per-grade subtotals by ladder, pairs in dataset read order.
    """

    window: str
    status: PackageStatus
    align_on: tuple[str, ...]
    pairs: tuple[AlignedPair, ...]
    variances: tuple[VarianceFlag, ...]
    escalations: tuple[UnmappedLine, ...]
    rollup: tuple[TargetRollup, ...]
    summary: PackageSummary


# --- Roll-up assembly (pure, deterministic) ---------------------------------


def _certainty_subtotals(pairs: tuple[AlignedPair, ...]) -> tuple[CertaintySubtotal, ...]:
    """Subtotal a target's actuals per certainty grade — exact, ladder-ordered, present-only.

    Sums `pair.actual.amount` per `pair.certainty` (the actual's grade, verbatim) in
    exact `Decimal`, emitting one `CertaintySubtotal` per grade **actually present**
    (never a fabricated `Decimal('0')` row for an absent grade) in ladder order
    (`_LADDER_ORDER`, most- to least-certain). A target with a mixed closed+open set
    yields two subtotals — the grade rides through, no cross-grade blending.
    """
    totals: dict[Certainty, Decimal] = {}
    counts: dict[Certainty, int] = {}
    for pair in pairs:
        grade = pair.certainty
        # `Decimal` throughout — the subtotal never touches `float`, so a target's
        # figure is exact currency, not a rounding artifact. `.get` defaults to the
        # exact `Decimal("0")` seed only for a grade that *is* present in this bucket.
        totals[grade] = totals.get(grade, Decimal("0")) + pair.actual.amount
        counts[grade] = counts.get(grade, 0) + 1
    return tuple(
        CertaintySubtotal(certainty=grade, actual_total=totals[grade], pair_count=counts[grade])
        for grade in sorted(totals, key=lambda g: _LADDER_ORDER[g])
    )


def _target_rollup(target: str, pairs: tuple[AlignedPair, ...]) -> TargetRollup:
    """Roll up one attribution target's pairs — actuals per grade, referents summed separately.

    `pairs` are the aligned pairs whose **actual** is attributed to `target`, in
    dataset read order. Subtotals the actuals per certainty grade
    (`_certainty_subtotals`), sums the matched budget referents into
    `budget_referent_total` **without differencing** them against the actuals, and
    flags `budget_referents_cross_target` when any pair's budget referent does not
    agree with `target` (a coarse-grain / cross-target / lump referent). Carries the
    source `pairs` by reference for drill-down. Basis is `ACTUALS_TO_DATE`.
    """
    # Budget referents summed verbatim and kept SEPARATE from the actual subtotals —
    # never subtracted, prorated, or percent-complete-adjusted (charter §2). This is
    # the sum of the *matched referents at grain `align_on`*, not the target's plan.
    budget_referent_total = sum((pair.budget.amount for pair in pairs), Decimal("0"))
    # A referent is "cross-target" when it does not agree with this bucket's target —
    # a *different* `attribution_target_id`. Under the coarse default grain such a pair
    # still aligns (account+period match), so the flag is how a reviewer sees the
    # referent isn't this target's own, never silently passed off as its authored
    # budget. (`ingest_and_align` escalates a lump `None`-target budget rather than
    # aligning one, so a `None` referent does not arise here; the `!=` handles it
    # defensively regardless, for a hand-built dataset.)
    cross_target = any(pair.budget.attribution_target_id != target for pair in pairs)
    return TargetRollup(
        attribution_target_id=target,
        actual_by_certainty=_certainty_subtotals(pairs),
        budget_referent_total=budget_referent_total,
        budget_referents_cross_target=cross_target,
        basis=ReportBasis.ACTUALS_TO_DATE,
        pairs=pairs,
    )


def _rollup(aligned: tuple[AlignedPair, ...]) -> tuple[TargetRollup, ...]:
    """Group the aligned pairs by the actual's attribution target — deterministic order.

    Groups on `pair.actual.attribution_target_id` (never `None` — an actual is always
    attributed upstream), preserving dataset read order within each group, and emits
    one `TargetRollup` per target with the target ids `sorted()`. The grouping
    partitions the aligned pairs: every pair lands in exactly one bucket, so the
    roll-up foots (`sum(t.pair_count) == len(aligned)`) and nothing is double-counted.
    """
    by_target: dict[str, list[AlignedPair]] = {}
    for pair in aligned:
        # An actual's `attribution_target_id` is `str`, never `None` (the Bookkeeper
        # resolves attribution upstream), so it is always a valid grouping key — a
        # cross-target *budget* lands the pair in the ACTUAL's bucket, not a `None` one.
        by_target.setdefault(pair.actual.attribution_target_id, []).append(pair)
    return tuple(
        _target_rollup(target, tuple(by_target[target])) for target in sorted(by_target)
    )


# --- The skill operation (pure, deterministic, sync — drives no port) -------


def build_report(
    dataset: AlignedDataset,
    variance_report: VarianceReport,
    config: AnalystConfig,
) -> ReportPackage:
    """Compose an `AlignedDataset` + `VarianceReport` into one PROPOSED `ReportPackage`.

    1. **Window-coherence fail-fast.** If `dataset.window != variance_report.window`
       the two inputs describe different analyses — a programming error, not a
       reviewable state. Raise `ValueError` naming **both** windows (the status stays
       `PROPOSED`-only; there is no `BLOCKED` package).
    2. **Compose verbatim.** Carry the input objects unchanged: `pairs =
       dataset.aligned`, `variances = variance_report.flags` (same objects,
       OVER-then-UNDER order preserved), `escalations = dataset.unmapped`, `window =
       dataset.window`. No delta is re-struck and **`flag_variance` is not called** —
       the report is an input, not something re-derived.
    3. **Per-target roll-up.** Group the aligned pairs by the **actual's**
       attribution target (targets `sorted()`), and for each subtotal the actuals per
       certainty grade (exact `Decimal`, ladder-ordered, present grades only), sum the
       matched budget referents **separately** into `budget_referent_total` (never
       differenced), flag `budget_referents_cross_target`, and carry the source pairs
       by reference. Basis is `ACTUALS_TO_DATE`.
    4. **Summary + return.** The self-describing counts, then the `PROPOSED`
       `ReportPackage` with `align_on` carried as grain metadata.

    Pure and **sync**: there is no source, sink, writer, or port in the signature, so
    the skill cannot read a system or mutate — it takes the two already-computed
    inputs and **assembles**. **Assembly-only** (§2): no narrative, no ranking, no
    driver attribution, no forecast/remaining/percent-consumed/EAC/run-rate/projected
    figure anywhere; the actuals are stated at `ACTUALS_TO_DATE` and the budget
    referents are carried honestly, never differenced. It mutates neither input and
    writes nothing canonical — the package is a note for a human, never a gate.
    """
    if dataset.window != variance_report.window:
        # The two inputs must describe the same analysis window; a mismatch means
        # a pair set and a flag set from different runs were composed by mistake.
        # Fail fast naming both windows — never assemble an incoherent package, and
        # never signal it as a reviewable `BLOCKED` state (there is no such status).
        raise ValueError(
            "build_report window mismatch: dataset.window "
            f"{dataset.window!r} != variance_report.window "
            f"{variance_report.window!r} — the aligned dataset and the variance "
            "report must describe the same analysis window."
        )

    # Compose verbatim — the input objects, unchanged, in their input order. The
    # tuples are immutable, so sharing the references is safe and makes the compose
    # provably identity-preserving (nothing re-struck, `flag_variance` never called).
    pairs = dataset.aligned
    variances = variance_report.flags
    escalations = dataset.unmapped

    rollup = _rollup(pairs)

    summary = PackageSummary(
        aligned_pairs=len(pairs),
        escalations=len(escalations),
        flagged_variances=len(variances),
        targets=len(rollup),
    )

    return ReportPackage(
        window=dataset.window,
        status=PackageStatus.PROPOSED,
        align_on=config.align_on,
        pairs=pairs,
        variances=variances,
        escalations=escalations,
        rollup=rollup,
        summary=summary,
    )
