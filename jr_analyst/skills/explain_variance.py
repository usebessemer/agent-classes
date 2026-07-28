"""`explain_variance` — the deterministic narrative substrate: strike + rank the story.

The slice-4 jr-analyst skill, downstream of `build_report` (slice 3). Where that
skill *composes* an `AlignedDataset` and a `VarianceReport` into a PROPOSED
`ReportPackage` — carrying the two budget/actual sides **honestly separate** and
deliberately **never differencing** them — this one reads that package and strikes
the story build_report left for slice 4: per target it **differences** the actuals
against the summed referents into one signed `target_variance`, **classifies** its
over/under `kind`, and **ranks** the target's flagged variances into an ordered
list of `VarianceDriver`s (largest `abs(delta)` first). It is the analyst's
`explain_variance` charter skill — Option A, the **deterministic** rung: it strikes
and ranks the variance that already exists; it authors **no prose** (the LLM
narrative rung is deferred) and forecasts **nothing** (charter §2, permanently
parked).

It mirrors the read-only skill family exactly where the pattern carries over —
**reader-only / proposes-not-writes**, **PROPOSED-only** (no new status; it reuses
`build_report`'s `PackageStatus.PROPOSED`), **compose-verbatim** (every driver
field is the input flag's, verbatim; every carried referent total is the roll-up's,
verbatim), **deterministic order**, **mutation-proven**, **pure + sync** (it drives
no port). It adds **no** `model.py` / `ports.py` / `config.py` surface: its frozen
result model is defined **inline here**, exactly as `build_report`'s and
`flag_variance`'s are.

The read-only §5-style boundary, preserved exactly (charter §1):

- **Strike + rank + propose, never write.** `explain_variance` *returns* an
  `ExplainedPackage`. It takes an already-composed `ReportPackage` and a config —
  **no source, no sink, no writer, no port** in its signature — so it structurally
  *cannot* read a system, mutate, or publish. It is pure and **sync**: a skill is
  async iff it drives a port, and this one drives none. It mutates neither input,
  re-runs no upstream skill, and blocks no downstream skill — the explanation is a
  note for a human, not a gate.
- **Every driver is traceable + graded.** A `VarianceDriver` carries its
  `VarianceFlag` **by reference** and exposes the flag's `delta` / `kind` /
  `certainty` and **both** `source_ref`s as verbatim `@property`s over that carried
  flag (the `AlignedPair.certainty` cannot-drift precedent, `model.py:136`), so a
  driver can never drift from the flag it explains and still links back to both
  source lines. The target `kind` classifies the variance that *exists*; the grade
  rides through untouched. Traceability, not "never wrong", is the trust wedge
  (charter §1).

🚧 **THE LINE — §2, deterministic-only (the boundary most likely to bloat).**
`explain_variance` is the slice-4 (narrative) side of the slice-3-vs-slice-4 line
`build_report` sat just short of: it **is** allowed to difference the two sides
into a `target_variance`, strike the over/under `kind` build_report refused, and
rank the drivers build_report would not. But it is **Option A** — the deterministic
rung — and the line still holds against the two things it must *not* do:

1. **No generated prose (the LLM narrative rung is deferred).** The explanation is
   **structural only**: an ordered tuple of drivers, three struck `Decimal`s, and an
   over/under enum. It authors **no** narrative / commentary / free-text sentence —
   a driver's only human-readable text is the **input flag's own** `reason`, carried
   verbatim through the referenced flag, never a sentence this skill wrote. Turning
   the ranked drivers into "marketing overran because…" prose is the parked LLM
   sub-slice, excluded here.
2. **No forecast (charter §2, permanently parked).** `target_variance` is struck
   from **actuals-to-date** (`sum` of the roll-up's per-grade actual subtotals) minus
   the **referents carried verbatim** — the variance that *already exists*, never a
   projected final. There is deliberately **no** remaining / percent-consumed / EAC /
   run-rate / projected / trend field anywhere on the result, and the forward-looking
   `committed` / `anticipated` rungs are never read (slice 1 yields only the two
   realized rungs the drivers carry). The drivers rank the variance that exists; they
   never extrapolate it.

`explain_variance` takes the `ReportPackage` as an **input** — it does **not** re-run
`build_report` or `flag_variance` (composition decision, charter): the flags, the
roll-up, and the referent totals are carried verbatim, no delta is re-struck, and a
package whose flags do not sit in the roll-up they claim is a programming error
(fail-fast `ValueError`, mirroring `build_report.py:362`), not a reviewable state.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from jr_analyst.config import AnalystConfig
from jr_analyst.model import Certainty
from jr_analyst.skills.build_report import PackageStatus, ReportPackage, TargetRollup
from jr_analyst.skills.flag_variance import VarianceFlag, VarianceKind

# Decimal zero, reused for the struck-total seeds and the sign classification (never
# coerced to `float`, so no total or comparison ever mixes `Decimal` and `float`),
# mirroring `flag_variance._ZERO` / `build_report`'s `Decimal("0")` seeds.
_ZERO = Decimal("0")


# --- The result model (deterministic, ranked, traceable, graded) -------------


@dataclass(frozen=True)
class VarianceDriver:
    """One ranked contributor to a target's variance — an explained `VarianceFlag`.

    Carries the implicated `VarianceFlag` **by reference** plus its 1-based `rank`
    within the target (largest `abs(delta)` first). Every explanatory field is a
    verbatim `@property` over the carried flag — `delta` / `kind` / `certainty` and
    **both** `source_ref`s — so a driver can never drift from the flag it explains
    (the `AlignedPair.certainty` cannot-drift precedent, `model.py:136`): a
    doctored-delta flag surfaces its doctored delta here verbatim, and the ladder
    grade rides through untouched. A driver is a note for a human, never a gate.
    """

    rank: int
    flag: VarianceFlag

    @property
    def delta(self) -> Decimal:
        """The flag's signed exact-`Decimal` delta, verbatim (never re-struck here)."""
        return self.flag.delta

    @property
    def kind(self) -> VarianceKind:
        """The flag's over/under bucket, verbatim."""
        return self.flag.kind

    @property
    def certainty(self) -> Certainty:
        """The flag's ladder grade (the actual's), verbatim — never `committed`/`anticipated`."""
        return self.flag.certainty

    @property
    def actual_source_ref(self) -> str:
        """The implicated actual's stable source id — the §1-traceable link, verbatim."""
        return self.flag.pair.actual.source_ref

    @property
    def budget_source_ref(self) -> str:
        """The implicated budget's stable source id — the §1-traceable link, verbatim."""
        return self.flag.pair.budget.source_ref


@dataclass(frozen=True)
class TargetExplanation:
    """One attribution target's struck-and-ranked variance story — deterministic, no prose.

    Strikes the variance `build_report` deferred, for the pairs in one `TargetRollup`:

    - `target_variance` — the signed exact-`Decimal` difference `sum(actuals-to-date)
      − budget_referent_total`, composed over the roll-up's **per-grade actual
      subtotals** (not a raw re-sum of `pair.actual.amount`). Actuals-to-date, never a
      projected final (§2).
    - `kind` — the over/under classification of that variance by sign, or `None` at
      exactly zero (there is no "on track" bucket, mirroring `flag_variance`'s no
      `ON_TRACK`). This is the target-level over/under verdict `build_report` refused;
      it classifies the variance that *exists*, it is not a forecast verdict.
    - `budget_referent_total` + `budget_referents_cross_target` — carried **verbatim**
      from the roll-up (the honest referent sum at grain `align_on`, and the flag that
      it mixes in a referent this target does not exclusively own), never re-derived.
    - `drivers` — the **full ranked list** of this target's flagged variances (largest
      `abs(delta)` first), no top-N. Each is a `VarianceDriver` over an input flag.
    - `flagged_delta_total` — the exact-`Decimal` sum of the drivers' (verbatim) deltas.
    - `subfloor_remainder` — the exact-`Decimal` sum of the signed deltas of this
      target's **unflagged** aligned pairs (those in `rollup.pairs` no driver covers).
      Struck **independently** from the pairs, so `target_variance ==
      flagged_delta_total + subfloor_remainder` is a proven identity, not the
      definition; an all-flagged target yields `Decimal("0")`.
    - `rollup` — the source `TargetRollup` **by reference**, so the per-grade
      breakdown and the source pairs can be drilled straight into (charter §1).

    Deterministic-only (§2): no narrative/prose/commentary and no forecast/remaining/
    percent-consumed/EAC/run-rate/projected field anywhere.
    """

    attribution_target_id: str
    target_variance: Decimal
    kind: VarianceKind | None
    budget_referent_total: Decimal
    budget_referents_cross_target: bool
    drivers: tuple[VarianceDriver, ...]
    flagged_delta_total: Decimal
    subfloor_remainder: Decimal
    rollup: TargetRollup


@dataclass(frozen=True)
class ExplainedPackage:
    """The slice-4 deliverable — the composed package plus its per-target explanations.

    What `explain_variance` returns. Carries the input `ReportPackage` **verbatim**
    (same object — nothing re-composed or re-struck) alongside the `explanations`
    tuple, one `TargetExplanation` per roll-up target in `package.rollup` (target-id)
    order. `window` is the package's, verbatim. `status` is `PackageStatus.PROPOSED`,
    **reused** from `build_report` (slice 4 adds no new status): the read-only analyst
    never publishes, files, or blocks. Deterministic throughout — two calls over the
    same package produce equal `ExplainedPackage`s.
    """

    package: ReportPackage
    window: str
    status: PackageStatus
    explanations: tuple[TargetExplanation, ...]


# --- Striking + ranking (pure, deterministic) --------------------------------


def _classify(variance: Decimal) -> VarianceKind | None:
    """Classify a signed variance by sign — over/under, or `None` at exactly zero.

    `> 0` → `OVER_BUDGET`, `< 0` → `UNDER_BUDGET`, `== 0` → `None` (there is no "on
    track" bucket — a zero variance has no side to report, mirroring `flag_variance`'s
    deliberate absence of an `ON_TRACK` kind).
    """
    if variance > _ZERO:
        return VarianceKind.OVER_BUDGET
    if variance < _ZERO:
        return VarianceKind.UNDER_BUDGET
    return None


def _rank_drivers(flags: tuple[VarianceFlag, ...]) -> tuple[VarianceDriver, ...]:
    """Rank a target's flags into `VarianceDriver`s — `abs(delta)` desc, then deterministic ties.

    Orders by the ranking key `(-abs(flag.delta), actual.source_ref,
    budget.source_ref, actual.account)`: largest magnitude first (the biggest
    contributor to the target's variance), ties broken by the two source ids then the
    account so the order is total and stable (a diffable review surface). The **full**
    ranked list is emitted — no top-N (that filtering would be an editorial judgment
    the deterministic rung does not make) — and `rank` is 1-based within the target.
    """
    ordered = sorted(
        flags,
        key=lambda flag: (
            -abs(flag.delta),
            flag.pair.actual.source_ref,
            flag.pair.budget.source_ref,
            flag.pair.actual.account,
        ),
    )
    return tuple(
        VarianceDriver(rank=index + 1, flag=flag) for index, flag in enumerate(ordered)
    )


def _explain_target(
    rollup: TargetRollup, flags: tuple[VarianceFlag, ...]
) -> TargetExplanation:
    """Strike one target's variance and rank its drivers — deterministic, actuals-to-date.

    `flags` are `package.variances` whose flagged pair's **actual** is attributed to
    this `rollup`'s target (the same grouping key `build_report` rolled up on). Strikes
    `target_variance` as `sum(per-grade actual subtotals) − budget_referent_total`
    (composed over the roll-up's subtotals, exact `Decimal`), classifies its `kind`,
    ranks the drivers, sums their (verbatim) deltas into `flagged_delta_total`, and
    strikes `subfloor_remainder` **independently** from the target's unflagged pairs —
    so the identity `target_variance == flagged_delta_total + subfloor_remainder`
    holds by proof, not by construction. Referent total + cross-target flag are carried
    verbatim from the roll-up; the roll-up rides on by reference.
    """
    # Struck over the per-grade subtotals — NOT a raw re-sum of `pair.actual.amount`.
    # (The two are equal by construction; composing over the subtotals is the honest
    # path — the roll-up already grade-separated the actuals, and this reuses that
    # partition rather than re-walking the pairs.) `budget_referent_total` is carried
    # verbatim from the roll-up: this is where slice 4 finally *differences* the two
    # sides build_report deliberately kept apart.
    actuals_to_date = sum(
        (subtotal.actual_total for subtotal in rollup.actual_by_certainty), _ZERO
    )
    target_variance = actuals_to_date - rollup.budget_referent_total

    drivers = _rank_drivers(flags)
    # The drivers' deltas, verbatim (a doctored delta rides through) — never re-struck.
    flagged_delta_total = sum((driver.delta for driver in drivers), _ZERO)

    # The unflagged remainder, struck INDEPENDENTLY from the pairs the drivers do not
    # cover — the signed `actual − budget` of every roll-up pair with no driver. Its
    # own quantity (equal to `target_variance − flagged_delta_total` by proof, not by
    # definition), so the footing identity is a real check, not a tautology. An
    # all-flagged target has no unflagged pairs, so this sums to `Decimal("0")`.
    driver_pairs = {driver.flag.pair for driver in drivers}
    subfloor_remainder = sum(
        (
            pair.actual.amount - pair.budget.amount
            for pair in rollup.pairs
            if pair not in driver_pairs
        ),
        _ZERO,
    )

    return TargetExplanation(
        attribution_target_id=rollup.attribution_target_id,
        target_variance=target_variance,
        kind=_classify(target_variance),
        budget_referent_total=rollup.budget_referent_total,
        budget_referents_cross_target=rollup.budget_referents_cross_target,
        drivers=drivers,
        flagged_delta_total=flagged_delta_total,
        subfloor_remainder=subfloor_remainder,
        rollup=rollup,
    )


def _flags_by_target(
    package: ReportPackage,
) -> dict[str, tuple[VarianceFlag, ...]]:
    """Group `package.variances` by the flagged pair's actual target — §3.4 fail-fast first.

    Groups on `flag.pair.actual.attribution_target_id` (the roll-up grouping key), and
    guards the two-grain malformed input `build_report` could never emit but a
    hand-built package could (mirroring `build_report.py:362`'s fail-fast):

    - **Orphan-target flag** — its target owns no `TargetRollup`. Raise `ValueError`
      naming the target (the flag claims a target the roll-up never grouped).
    - **Orphan-pair flag** — its target *has* a roll-up, but the flagged `pair` is not
      among that roll-up's `pairs`. Raise `ValueError` naming the target **and** both
      of the pair's `source_ref`s (the flag claims a pair its own target's roll-up
      does not carry).

    Either is a programming error (a package whose flags and roll-up disagree), not a
    reviewable state — so it fails fast rather than silently attributing a driver to a
    target it does not belong to.
    """
    rollup_by_target = {rollup.attribution_target_id: rollup for rollup in package.rollup}
    grouped: dict[str, list[VarianceFlag]] = {}
    for flag in package.variances:
        target = flag.pair.actual.attribution_target_id
        rollup = rollup_by_target.get(target)
        if rollup is None:
            raise ValueError(
                "explain_variance orphan-target flag: the flagged pair's actual is "
                f"attributed to target {target!r}, which no roll-up in the package "
                "carries — the variances and the roll-up describe different targets."
            )
        if flag.pair not in rollup.pairs:
            raise ValueError(
                "explain_variance orphan-pair flag: the flagged pair "
                f"(actual {flag.pair.actual.source_ref!r}, budget "
                f"{flag.pair.budget.source_ref!r}) is not among target {target!r}'s "
                "roll-up pairs — the flag claims a pair its target's roll-up does not "
                "carry."
            )
        grouped.setdefault(target, []).append(flag)
    return {target: tuple(flags) for target, flags in grouped.items()}


# --- The skill operation (pure, deterministic, sync — drives no port) -------


def explain_variance(package: ReportPackage, config: AnalystConfig) -> ExplainedPackage:
    """Strike + rank one `ReportPackage`'s per-target variance story — reader-only.

    1. **Two-grain fail-fast.** Group `package.variances` by the flagged pair's actual
       target. If any flag's target owns no roll-up (orphan-target) or its pair is not
       in that target's roll-up pairs (orphan-pair), raise `ValueError` — a package
       whose flags and roll-up disagree is a programming error, not a reviewable state
       (mirroring `build_report`'s window-coherence fail-fast).
    2. **Per-target strike + rank.** For each roll-up in `package.rollup` (already in
       target-id order) strike `target_variance = sum(per-grade actual subtotals) −
       budget_referent_total` (exact `Decimal`, actuals-to-date), classify its over/
       under `kind` (`None` at zero), rank the target's flags into the **full** driver
       list (largest `abs(delta)` first, 1-based `rank`), sum the drivers' verbatim
       deltas into `flagged_delta_total`, and strike `subfloor_remainder`
       independently from the unflagged pairs. Referent total + cross-target flag ride
       through verbatim.
    3. **Return.** The `ExplainedPackage` — the input package verbatim, its `window`,
       the reused `PackageStatus.PROPOSED`, and the explanations in roll-up order.

    Pure and **sync**: there is no source, sink, writer, or port in the signature, so
    the skill cannot read a system or mutate — it takes the already-composed package
    and **explains** it. **Deterministic-only** (Option A, §2): it strikes and ranks
    the variance that already exists — no generated prose (a driver's only text is the
    input flag's own `reason`, verbatim) and no forecast/remaining/percent-consumed/
    EAC/run-rate/projected figure anywhere; the `committed` / `anticipated` rungs are
    never read. It mutates neither input and writes nothing canonical — the
    explanation is a note for a human, never a gate.

    `config` is carried for skill-family signature consistency (and the deferred
    LLM-prose sub-slice, which will read it for tone/limits); the deterministic strike
    reads nothing from it — the flagging that `variance_floor` gated already happened
    upstream in `flag_variance`, and the alignment grain `align_on` already rode onto
    the package. It is threaded through unused rather than dropped so the public skill
    signature stays uniform across the read-only family.
    """
    flags_by_target = _flags_by_target(package)

    explanations = tuple(
        _explain_target(rollup, flags_by_target.get(rollup.attribution_target_id, ()))
        for rollup in package.rollup
    )

    return ExplainedPackage(
        package=package,
        window=package.window,
        status=PackageStatus.PROPOSED,
        explanations=explanations,
    )
