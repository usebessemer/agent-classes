"""`flag_variance` — the per-pair watchdog: surface material actual-vs-budget gaps.

The slice-2 jr-analyst skill, downstream of `ingest_and_align`. Where that skill
*aligns* a window's certainty-graded actuals to budget 1:1, this one *reads the
aligned pairs* and computes, for each, the signed actual-vs-budget delta —
surfacing the ones that clear the materiality floor for a human's planning
review. It is the analyst's counterpart to the Bookkeeper's `flag_anomaly`: the
lowest-risk skill on the read-only core — it **proposes, never acts**.

The read-only §5-style boundary, preserved exactly (charter §1):

- **Reader-only / proposes-not-writes.** `flag_variance` *returns* a
  `VarianceReport`. It takes an already-computed `AlignedDataset` and a config —
  **no source, no sink, no writer, no port** in its signature — so it structurally
  *cannot* read a system, mutate, or publish. It is pure and **sync**: a skill is
  async iff it drives a port, and this one drives none. It mutates neither input,
  never resolves / suppresses / acts on a variance, and never blocks a downstream
  skill — a flag is a note for a human, not a gate.
- **Every flag is traceable + graded.** Each `VarianceFlag` links back to both
  sides via their `source_ref`s and carries the aligned pair's certainty grade
  **verbatim** (`pair.certainty`, i.e. the actual's grade) — a `realized_open`
  variance is surfaced graded open, never flattened. Traceability, not
  "never wrong", is the trust wedge (charter §1).

🚧 **THE LINE — §2, non-forecasting (the boundary most likely to bloat).**
`flag_variance` reports the variance that **already exists** between a realized
actual and its budget — a *snapshot*, decided from the two aligned amounts and
nothing else. It is **forward-looking, not a forecast**: it grades how settled the
figure is (via the pair's ladder certainty), it never predicts tomorrow's. There
is deliberately **no trend, no run-rate, no projection, no EAC (estimate-at-
completion), no percent-complete, no scenario or predictive modelling, and no
"this pair is trending over budget"** — that inferential CFO overlay is excluded
by charter §2 (the parked forecasting rung). Concretely, the line shows up as
three pins:

1. A `realized_open` pair is reported at **actuals-to-date** — its signed delta,
   with **no projected final number** grafted on. Open means in-flight, not
   forecast-to-completion.
2. `budget.amount` is the subtrahend **verbatim** (a referent-provenance pin): it
   is never prorated, percent-complete-adjusted, or run-rated to "what the budget
   should be by now". The plan figure is used exactly as the budget source gave
   it.
3. The forward-looking ladder rungs a later slice carries (`committed`,
   `anticipated`) are **never read** here — this skill sees the two realized rungs
   an `AlignedPair` already holds, nothing it would have to model.

The delta is a signed **exact `Decimal`** (`actual.amount - budget.amount`),
never `abs`-stored and never `float`, mirroring `ReconciliationGap.delta`: the
sign *is* the over/under classification, and an exact difference is a real
variance, never a float-rounding artifact. There is deliberately **no ratio and
no percent** — a variance is a signed money delta, not a computed rate.

**The materiality floor, inverted-when-unset (charter §3/§5.4).** `config.
variance_floor` is the one surfacing knob: a variance is surfaced when
`abs(delta)` is **strictly over** it. Unset (`None`) the default is *inverted*
from the Bookkeeper's over-materiality check — where an unset floor there *skips*
the check, here an unset floor surfaces **every** non-zero-delta pair (none
suppressed): for a read-only analyst "inert" means *show everything*, never
*hide everything*. A zero delta is never surfaced (there is no variance to flag),
and a delta exactly equal to the floor is suppressed (the comparison is strict
`>`).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from jr_analyst.config import AnalystConfig
from jr_analyst.model import AlignedDataset, AlignedPair, Certainty

# Decimal zero, reused for the no-variance check and the sign classification
# (never coerced to `float`, so the comparison never mixes `Decimal` and `float`).
_ZERO = Decimal("0")


# --- The result model (advisory, traceable, graded) -------------------------


class VarianceKind(str, Enum):
    """Which side of budget a surfaced variance falls on (exactly one).

    A `str` enum (like `UnmappedKind` / the Bookkeeper's `GapKind`) so the kind
    serializes to a stable, readable tag for the run log and the review surface.
    There is deliberately **no `ON_TRACK`**: a flag *is* a surfaced material
    variance, so a zero (or immaterial) delta produces no flag at all rather than
    an "on track" one.
    """

    #: `actual.amount > budget.amount` — spend over the plan (signed delta > 0).
    OVER_BUDGET = "over_budget"
    #: `actual.amount < budget.amount` — spend under the plan (signed delta < 0).
    UNDER_BUDGET = "under_budget"


@dataclass(frozen=True)
class VarianceFlag:
    """One surfaced actual-vs-budget variance — advisory, never acted on.

    `kind` is the over/under bucket; `delta` is the **signed, exact `Decimal`**
    `actual.amount - budget.amount` (never `abs`-stored, never `float` — mirroring
    `ReconciliationGap.delta`), so the sign carries the classification and the
    magnitude is exact. `certainty` is the aligned pair's ladder grade **verbatim**
    (`pair.certainty`, the actual's grade), so a `realized_open` variance is
    surfaced graded open, never flattened. `reason` is the human-readable
    §1-traceable why — it embeds both `source_ref`s, both amounts, the delta, and
    the grade. `pair` is the implicated `AlignedPair`, linking the flag back to
    both source lines. A flag is a note for a human, never a gate: the skill
    mutates nothing and blocks nothing.
    """

    kind: VarianceKind
    delta: Decimal
    certainty: Certainty
    reason: str
    pair: AlignedPair


@dataclass(frozen=True)
class VarianceReport:
    """An **advisory** scan of one window's aligned pairs for material variances.

    Writes nothing (§5): `flag_variance` returns this; it stores nothing to the
    ledger, the budget, the system of record, or anywhere canonical, and never
    blocks a downstream skill. Carries the analysis `window` (the dataset's,
    verbatim) and the deterministic `flags` tuple — grouped by kind in a fixed
    order, `OVER_BUDGET` then `UNDER_BUDGET`, and within each kind in the
    dataset's `aligned` read order (mirroring `AnomalyReport`'s grouped ordering)
    for stable, diffable review.
    """

    window: str
    flags: tuple[VarianceFlag, ...]


# --- Traceable variance reason (charter §1) ---------------------------------


def _variance_reason(pair: AlignedPair, delta: Decimal, kind: VarianceKind) -> str:
    """The §1-traceable why a variance is surfaced — both sides, both amounts, the delta.

    Embeds both `source_ref`s, both amounts, the signed delta, and the pair's grade
    (`pair.certainty.value`), so the flag links back to the exact source lines it
    came from and states the arithmetic in the open (in the `ingest_and_align`
    escalation-reason style). `budget.amount` appears verbatim as the subtrahend —
    never prorated or percent-complete-adjusted (the referent-provenance pin, §2).
    """
    actual = pair.actual
    budget = pair.budget
    direction = "over" if kind is VarianceKind.OVER_BUDGET else "under"
    return (
        f"Actual {actual.source_ref!r} ({actual.amount}, grade "
        f"{pair.certainty.value}) is {direction} budget {budget.source_ref!r} "
        f"({budget.amount}) by {delta} (signed actual − budget). Surfaced for a "
        f"human's planning review — advisory, never acted on (charter §1)."
    )


# --- The skill operation (pure, deterministic, sync — drives no port) -------


def flag_variance(dataset: AlignedDataset, config: AnalystConfig) -> VarianceReport:
    """Surface each aligned pair's material actual-vs-budget variance — reader-only.

    1. Iterate `dataset.aligned` (only — the escalated `dataset.unmapped` remainder
       is **not** read or re-flagged; assembling that queue is slice 3). For each
       pair compute the signed exact-`Decimal` delta `actual.amount -
       budget.amount`; classify by sign (`> 0` → `OVER_BUDGET`, `< 0` →
       `UNDER_BUDGET`).
    2. Surface by materiality: a zero delta is never surfaced (no variance to
       flag). With `config.variance_floor` **set**, surface a pair iff `abs(delta)`
       is **strictly over** the floor (a delta exactly at the floor is suppressed).
       With it **unset** (`None`), the default is *inverted* — surface **every**
       non-zero-delta pair, none suppressed (charter §3/§5.4).
    3. Return the `VarianceReport`, `window` carried from the dataset — flags
       grouped `OVER_BUDGET` then `UNDER_BUDGET`, each in `dataset.aligned` read
       order. **Advisory: writes nothing, mutates neither input, blocks no
       downstream skill** — resolving any variance is a later, human decision.

    Pure and **sync**: there is no source, sink, writer, or port in the signature,
    so the skill cannot read a system or mutate — it takes the already-aligned
    dataset and proposes. **Non-forecasting** (§2): the delta is the variance that
    already exists between the two aligned amounts — no trend, run-rate, EAC, or
    projection; `budget.amount` is used verbatim as the subtrahend, and the
    forward-looking `committed` / `anticipated` rungs are never read.
    """
    floor = config.variance_floor

    over: list[VarianceFlag] = []
    under: list[VarianceFlag] = []
    for pair in dataset.aligned:
        delta = pair.actual.amount - pair.budget.amount
        if delta == _ZERO:
            # No variance to flag — a flag *is* a surfaced material variance, so a
            # zero delta produces no flag (there is no `ON_TRACK` kind).
            continue
        if floor is not None and abs(delta) <= floor:
            # Below or exactly at the materiality floor — suppressed (strict `>`).
            # Only when the floor is *set*: an unset floor surfaces every non-zero
            # delta (the inverted default), so this suppression never applies then.
            continue
        kind = VarianceKind.OVER_BUDGET if delta > _ZERO else VarianceKind.UNDER_BUDGET
        flag = VarianceFlag(
            kind=kind,
            delta=delta,
            certainty=pair.certainty,
            reason=_variance_reason(pair, delta, kind),
            pair=pair,
        )
        (over if kind is VarianceKind.OVER_BUDGET else under).append(flag)

    return VarianceReport(window=dataset.window, flags=tuple(over + under))
