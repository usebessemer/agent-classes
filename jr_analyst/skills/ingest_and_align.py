"""`ingest_and_align` — align certainty-tagged realized actuals to the budget 1:1.

The slice-1 jr-analyst skill, and the analyst counterpart to the Bookkeeper's
`reconcile_account`: an **async driver** (`ingest_and_align`) that reads both
sides through the read-only source ports, over a **pure core** (`_align`) that
matches and partitions with no I/O. It reads a window's realized actuals (via
`ActualsSource`) and that period's budget targets (via `BudgetSource`), aligns
each actual one-to-one to the budget target it belongs to, and escalates
everything that cannot align — returning an `AlignedDataset` for human review.

This is the **forward-looking** core: it exists to see **in-flight open costs**.
Both realized rungs flow through unchanged — a `realized_open` actual aligns and
its pair is graded open, exactly as a `realized_closed` one does; slice 1 never
flattens the ladder to a single "actual" bucket (`AlignedPair.certainty` exposes
`actual.certainty` verbatim, so the grade cannot drift).

The read-only §5-style boundary, preserved exactly (charter §1):

- **Detection-only — mutates nothing.** The skill reads two sources and *returns
  a dataset*. There is no sink, no writer, no system-of-record handle anywhere in
  this module — structurally it *cannot* publish. The only source-touching
  arguments are the read-side `ActualsSource` / `BudgetSource`. A test pins the
  fakes recorded only reads.
- **Resolution is always human.** The skill never allocates a lump budget across
  jobs, never invents a missing budget, never drops an uncategorized line — it
  only *surfaces*. Every non-aligned line becomes an `UnmappedLine` tagged with
  why; routing those to a review surface is a later, gated step, not the skill's.

**The alignment — two sides, one partition, count-conserving.** Every actual and
every budget line lands in **exactly one** of `{aligned, unmapped}` (the partition
is disjoint and total — nothing dropped, nothing double-counted):

1. **Uncategorized-open actuals escalate first.** An adapter-surfaced actual with
   **no resolved account** (a blank `account`) cannot be keyed to a budget — it is
   the **capture-completeness signal**, a hole in the books a forward-looking
   analyst must flag, so it becomes an `UNCATEGORIZED_OPEN` `UnmappedLine`, never
   dropped and never matched.
2. **Lump budgets escalate — grain mismatch is a human judgment.** A budget with
   `attribution_target_id is None` is set at the **account level**, not allocated
   to a §3 target. Allocating that lump across the attribution-grain actuals it
   might cover is a human judgment, not the skill's, so a lump budget never enters
   the match; it becomes an `UNMAPPED_BUDGET` `UnmappedLine` (grain-mismatch
   reason).
3. **Everything else aligns 1:1 on the configured key.** Each remaining actual is
   matched to the first still-free attribution-grain budget line sharing its
   `config.align_on` key (default `(account, period)`; an instance that budgets
   per §3 target configures the finer `(account, attribution_target_id, period)`).
   The match is strictly one-to-one — each budget line is consumed once — and the
   pair carries **both real lines**, never a zero-side pad or a fabricated
   `Decimal("0")`.

Whatever is still unpaired after the match is one-sided and surfaced: an actual
with no budget target is an `UNMATCHED_ACTUAL` (spend the plan did not
anticipate); an attribution-grain budget with no actual is an `UNMAPPED_BUDGET`
(no-matching-actual reason). Each escalated line keeps its own `source_ref` (and,
for an actual, its `certainty`), so an escalation is as traceable as an aligned
pair (charter §1).

**Decimal money, exact.** The skill never does money arithmetic — it aligns lines
by key and hands both amounts to the downstream variance step untouched — so no
value is ever coerced to `float` or padded with a fabricated `Decimal("0")`; a
later variance is a *real* difference, never a rounding artifact.
"""

from __future__ import annotations

from jr_analyst.config import AnalystConfig
from jr_analyst.model import (
    ActualLine,
    AlignedDataset,
    AlignedPair,
    BudgetLine,
    UnmappedKind,
    UnmappedLine,
)
from jr_analyst.ports import ActualsSource, BudgetSource


# --- The alignment key (built from the configured grain) --------------------


def _align_key(line: ActualLine | BudgetLine, align_on: tuple[str, ...]) -> tuple[object, ...]:
    """The tuple `ingest_and_align` matches an actual to its budget on.

    Read straight off `config.align_on` (default `(account, period)`), so the
    instance's configured grain — not the framework — decides how fine the match
    is. Both `ActualLine` and `BudgetLine` carry every default `align_on` field, so
    the same key builds for either side and a match is exact tuple equality.
    """
    return tuple(getattr(line, field) for field in align_on)


def _has_account(actual: ActualLine) -> bool:
    """Whether an actual has a resolved account — else it is uncategorized-open.

    A blank `account` (empty or whitespace) is the adapter's capture-completeness
    signal: an open line the pipeline surfaced but could not resolve to an account,
    so it cannot be keyed to a budget. Guarded defensively against `None` (the
    field is typed `str`, but an adapter is a foreign boundary) — mirrors the
    `(label or "").strip()` guard in `certainty.py`.
    """
    return bool((actual.account or "").strip())


# --- Traceable escalation reasons (charter §1) ------------------------------


def _uncategorized_open_reason(actual: ActualLine) -> str:
    """The §1-traceable why an actual with no account is surfaced, not aligned."""
    return (
        f"Actual {actual.source_ref!r} ({actual.amount}, period {actual.period!r}, "
        f"grade {actual.certainty.value}) has no resolved account — an "
        f"adapter-surfaced open line the pipeline could not categorize. It cannot "
        f"be keyed to a budget, so it is surfaced as a capture-completeness hole "
        f"for human resolution, never dropped (charter §1)."
    )


def _unmatched_actual_reason(actual: ActualLine) -> str:
    """The §1-traceable why a real actual with no budget target is surfaced."""
    return (
        f"Actual {actual.source_ref!r} ({actual.amount}, account "
        f"{actual.account!r}, period {actual.period!r}, grade "
        f"{actual.certainty.value}) has no matching budget target — spend the plan "
        f"did not anticipate. Surfaced for human resolution, never aligned to a "
        f"fabricated zero-budget (charter §1)."
    )


def _unmapped_budget_reason(budget: BudgetLine) -> str:
    """The §1-traceable why a budget line is surfaced — grain vs no-matching-actual."""
    if budget.attribution_target_id is None:
        return (
            f"Budget {budget.source_ref!r} ({budget.amount}, account "
            f"{budget.account!r}, period {budget.period!r}) is a lump "
            f"account-grain target (no attribution target). Allocating it across "
            f"the attribution-grain actuals it may cover is a human judgment, not "
            f"the skill's, so the grain mismatch is surfaced for human resolution, "
            f"never guessed (charter §1)."
        )
    return (
        f"Budget {budget.source_ref!r} ({budget.amount}, account "
        f"{budget.account!r}, target {budget.attribution_target_id!r}, period "
        f"{budget.period!r}) has no matching actual — planned spend with nothing "
        f"realized against it. Surfaced for human resolution (charter §1)."
    )


# --- The aligner (pure, deterministic) --------------------------------------


def _align(
    actuals: list[ActualLine],
    budgets: list[BudgetLine],
    align_on: tuple[str, ...],
) -> tuple[list[AlignedPair], list[UnmappedLine]]:
    """Align actuals to budgets 1:1 on `align_on`; partition the rest, escalated.

    Pure and deterministic over its three inputs (no I/O, mutates neither list).
    Every actual and every budget lands in exactly one of the two returned lists
    (count conservation): `2 * len(aligned) + len(unmapped)` equals
    `len(actuals) + len(budgets)`.

    The order is stable and grouped by kind for a diffable review surface: aligned
    pairs in actual read order; then the escalations — `unmatched_actual` and
    `uncategorized_open` (both in actual read order), then `unmapped_budget` (in
    budget read order).
    """
    budget_used = [False] * len(budgets)

    aligned: list[AlignedPair] = []
    unmatched_actuals: list[UnmappedLine] = []
    uncategorized_open: list[UnmappedLine] = []

    # Actuals in read order. A blank-account line is the capture-completeness
    # signal (escalated, never matched); every other actual seeks the first
    # still-free attribution-grain budget sharing its key. Lump budgets
    # (`attribution_target_id is None`) are never matched here — allocating a lump
    # across jobs is a human judgment — so they fall through to `unmapped_budget`.
    for actual in actuals:
        if not _has_account(actual):
            uncategorized_open.append(
                UnmappedLine(
                    line=actual,
                    kind=UnmappedKind.UNCATEGORIZED_OPEN,
                    reason=_uncategorized_open_reason(actual),
                )
            )
            continue

        key = _align_key(actual, align_on)
        match_index: int | None = None
        for bi, budget in enumerate(budgets):
            if budget_used[bi] or budget.attribution_target_id is None:
                continue
            if _align_key(budget, align_on) == key:
                match_index = bi
                break

        if match_index is None:
            unmatched_actuals.append(
                UnmappedLine(
                    line=actual,
                    kind=UnmappedKind.UNMATCHED_ACTUAL,
                    reason=_unmatched_actual_reason(actual),
                )
            )
        else:
            budget_used[match_index] = True
            aligned.append(AlignedPair(actual=actual, budget=budgets[match_index]))

    # Leftover budgets — one per bucket, in budget read order. A lump budget is a
    # grain mismatch; an attribution-grain leftover had no matching actual. Both
    # are `UNMAPPED_BUDGET` (the reason distinguishes them).
    unmapped_budgets = [
        UnmappedLine(
            line=budget,
            kind=UnmappedKind.UNMAPPED_BUDGET,
            reason=_unmapped_budget_reason(budget),
        )
        for bi, budget in enumerate(budgets)
        if not budget_used[bi]
    ]

    unmapped = unmatched_actuals + uncategorized_open + unmapped_budgets
    return aligned, unmapped


# --- The skill operation ----------------------------------------------------


async def ingest_and_align(
    actuals_source: ActualsSource,
    budget_source: BudgetSource,
    config: AnalystConfig,
    window: str,
) -> AlignedDataset:
    """Align `window`'s realized actuals to its budget 1:1 — read-only, detection-only.

    1. `actuals_source.fetch_realized(window)` + `budget_source.fetch_budget(window)`
       — read both sides, write nothing. In slice 1 the window *is* the budget
       period, so the same label queries both.
    2. Escalate what cannot align: an actual with no account
       (`uncategorized_open`, the capture-completeness signal) and a lump
       account-grain budget (`unmapped_budget`, a grain mismatch a human resolves).
    3. Align every remaining actual 1:1 to the first still-free attribution-grain
       budget sharing its `config.align_on` key — both realized rungs flow through
       unchanged, each pair carrying the actual's grade verbatim. Leftover actuals
       → `unmatched_actual`; leftover budgets → `unmapped_budget`.
    4. Return the `AlignedDataset` (partition disjoint + total) — detection-only,
       **writes nothing canonical**; resolving any escalation is a later,
       human-gated step.

    The only source-touching arguments are the read-side `ActualsSource` /
    `BudgetSource` — there is no writer of any kind, so the skill cannot mutate.
    `config` supplies only the alignment grain (`align_on`); a read-only analyst
    has no autonomy threshold to arm, so nothing else is read here.
    """
    actuals = await actuals_source.fetch_realized(window)
    budgets = await budget_source.fetch_budget(window)

    aligned, unmapped = _align(actuals, budgets, config.align_on)

    return AlignedDataset(
        window=window,
        aligned=tuple(aligned),
        unmapped=tuple(unmapped),
    )
