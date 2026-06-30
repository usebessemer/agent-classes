"""`closePeriod` — assemble the period and propose it for human sign-off.

The §5.7 skill, the composition step that sits over the period-close groundwork
(capture → attribute → categorize → trackTax → reconcile). It takes the period's
**already-produced reports** and either produces a **proposed close** (all the
close preconditions met) or **blocks with the open items listed** (anything
outstanding). It composes; it does not orchestrate — the caller runs the other
skills and hands their reports in.

The §5 boundary, preserved exactly (§5.7 — *the agent assembles and proposes; the
human signs*):

- **Assemble + propose, never sign.** `close_period` *returns* a proposed close;
  it writes nothing to the ledger, the system of record, the review queue, or
  `prior_period_state`, and it **never marks the period closed or signed**. There
  is no status that means "closed": the result is `READY` (a proposal awaiting a
  human's signature) or `BLOCKED` — never a signable-by-the-agent state. The
  function takes no sink, writer, queue, or any write-capable port, so it
  *cannot* publish. A test pins it writes nothing canonical.
- **Block, never silently close.** Any unmet precondition → `BLOCKED`, with every
  open item listed as a blocker. A signable close is **never** produced over an
  open item — `proposed_close` is `None` whenever `BLOCKED`.
- **Never edit filed prior-period state.** `config.prior_period_state` is
  read-only here. A period at or before the last close is refused (`BLOCKED`),
  never re-closed or edited; `config` and `prior_period_state` are never mutated.

**The v1 resolution model (the honest consequence of detection/propose-only).**
The human-resolution / interaction surface — confirming proposals, resolving gaps
— **isn't built yet**. So in v1 there is no separate "resolved" flag: the open
items *are* the reports' open items, and "resolution" means the human acts on the
underlying data / config out-of-band and the **pipeline is re-run**, producing
reports without those items. `close_period` is therefore a **pure checklist over
the current reports** — it re-runs to clear. The persistent-confirmation store is
a future interaction-surface piece and is deliberately *not* built here.

**Preconditions (BLOCK the close on any unmet one — fail-safe, never silent).**
Checked over the three reports + the period, in a fixed order:

1. **Period is closeable vs prior state** — the `period` is strictly *after*
   `config.prior_period_state`'s last close. Periods are treated as opaque,
   lexicographically-ordered labels (ISO-style, year-first and zero-padded:
   ``2026-Q1`` < ``2026-Q2``, ``2026-01`` < ``2026-02``); an unset
   `prior_period_state` means no period has closed yet, so any period is
   closeable. A period at or before the prior close is refused (§5: never edit
   filed prior-period state).
2. **Reconciliation clean** — no open `gaps` *and* no `to_confirm` in the
   `ReconciliationReport`. Any of either → blocker.
3. **No un-categorizable lines** — no `flagged` in the `CategorizationReport`
   (the genuinely-unresolved, never-matched ones). Category **proposals do not
   block**: the human signing the close confirms them; only `flagged` lines block.
4. **Tax clean** — no `flagged` in the `TaxSummary` (e.g. tax captured with no
   target). The summary having been produced means the totals were struck.

All met → `READY`: assemble + return the proposed close. Any unmet → `BLOCKED`:
return the report listing every blocker.

**Pure, deterministic, port-free.** `close_period` reads no source — it is a pure
function of the reports + config + period, so it is sync (no `await`), trivial to
test, and cannot mutate anything. Ordering is deterministic: the checklist is
always the four checks in the order above; the blockers follow the reports' own
deterministic order.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from bookkeeper.config import BookkeeperConfig
from bookkeeper.skills.categorize import CategorizationReport, CategoryFlag
from bookkeeper.skills.reconcile import (
    PairToConfirm,
    ReconciliationGap,
    ReconciliationReport,
)
from bookkeeper.skills.track_tax import TaxFlag, TaxSummary

# The precondition names recorded on every checklist item and blocker, so a human
# reading the report can see exactly which §5.7 precondition each line is about
# (charter §1: fully traceable). Plain string tags, fixed order.
CHECK_PERIOD_CLOSEABLE = "period_closeable"
CHECK_RECONCILIATION_CLEAN = "reconciliation_clean"
CHECK_CATEGORIZATION_COMPLETE = "categorization_complete"
CHECK_TAX_CLEAN = "tax_clean"


# --- The result model (proposed, traceable) --------------------------------


class CloseStatus(str, Enum):
    """Whether the period is ready to propose for sign-off, or blocked.

    A `str` enum (like `GapKind` / `RunOutcome`) so the status serializes to a
    stable, readable tag. There is **deliberately no** ``CLOSED`` / ``SIGNED``
    member: `close_period` never signs — the most it produces is a `READY`
    proposal awaiting a human's signature (§5.7).
    """

    #: Every precondition met — a proposed close is assembled and returned.
    READY = "ready"
    #: At least one precondition unmet — no signable close; the blockers are listed.
    BLOCKED = "blocked"


@dataclass(frozen=True)
class CloseCheck:
    """One close precondition with its met/unmet verdict and a traceable reason.

    The checklist always carries all four checks (met or not), so the report
    shows the full picture, not only the failures — the human sees what passed as
    well as what blocked (charter §1: fully traceable). `name` is one of the
    ``CHECK_*`` tags.
    """

    name: str
    met: bool
    reason: str


@dataclass(frozen=True)
class CloseBlocker:
    """One specific open item blocking the close, traceable to its source report.

    `check` is the precondition it failed (a ``CHECK_*`` tag); `reason` is the
    §1-traceable human-readable why; `item` is the underlying open item — a
    reconciliation gap or to-confirm pair, a category flag, or a tax flag, each
    of which already links back to its own source — or `None` for the
    prior-period guard (which is about the period itself, not a report item).
    """

    check: str
    reason: str
    item: ReconciliationGap | PairToConfirm | CategoryFlag | TaxFlag | None = None


@dataclass(frozen=True)
class PeriodSummary:
    """The period's disposition counts for the close (Contract A's summary).

    `processed` is every transaction the period covered; `auto_filed` the ones the
    pipeline handled confidently (a confident category proposal); `reviewed` the
    ones it surfaced for a human (a category flag); `open` the total outstanding
    items across all three reports. The counts reconcile —
    ``processed == auto_filed + reviewed`` — and a `READY` close has ``open == 0``
    by construction (that is exactly what the preconditions check).

    **v1 honesty.** Until the interaction surface exists, `close_period` sees only
    the three computation reports, not the intake-time auto-file/review
    disposition (which lives in the orchestrator's run log, not here). So the
    auto-filed/reviewed split is read from the *final* reports' own buckets — a
    confident proposal is "auto-filed", a flag "reviewed" — and a clean `READY`
    close legitimately shows everything auto-filed and nothing open, because the
    human resolved any exceptions out-of-band and the pipeline was re-run. A
    richer intake-time disposition feed is a future piece.
    """

    processed: int
    auto_filed: int
    reviewed: int
    open: int


@dataclass(frozen=True)
class AssembledPeriod:
    """The period assembled from its reports — the input to `generateAccountantPackage`.

    The attribution-costed, categorized, tax-broken-out, reconciled period, held
    as the three source reports verbatim (assembly, not re-formatting): the next
    skill (`generateAccountantPackage`) formats this to the instance's
    `accountant_format`. `close_period` stops here — it composes, it does not
    format. Every figure stays linked to the report it came from, each of which
    links to its source artifacts (charter §1: fully traceable).
    """

    period: str
    reconciliation: ReconciliationReport
    tax_summary: TaxSummary
    categorization: CategorizationReport


@dataclass(frozen=True)
class ProposedClose:
    """A period proposed for sign-off — **proposed, never signed** (§5.7).

    Present only on a `READY` `CloseReport`. Carries the `summary` (the
    disposition counts, `open == 0`) and the `assembled` period. It is a
    proposal: a human signs the period closed — `close_period` never does, and
    writes nothing.
    """

    summary: PeriodSummary
    assembled: AssembledPeriod


@dataclass(frozen=True)
class CloseReport:
    """The result of `close_period` — a proposed close or the blockers (§5.7).

    Proposed, never signed, writes nothing (§5.7): `close_period` returns this; it
    writes nothing to the ledger, the system of record, the queue, or
    `prior_period_state`, and never marks the period closed. `checklist` always
    carries all four preconditions (met or not). When `status` is `READY`,
    `proposed_close` is the assembled proposal and `blockers` is empty; when
    `BLOCKED`, `proposed_close` is `None` (never a signable close over an open
    item) and `blockers` lists every outstanding item. Ordering is deterministic.
    """

    period: str
    status: CloseStatus
    checklist: tuple[CloseCheck, ...]
    blockers: tuple[CloseBlocker, ...] = field(default_factory=tuple)
    proposed_close: ProposedClose | None = None


# --- The precondition checks (pure, deterministic) --------------------------


def _check_period_closeable(
    period: str, prior_period_state: str | None
) -> tuple[CloseCheck, CloseBlocker | None]:
    """Precondition 1 — the period is strictly after the last closed period.

    An unset `prior_period_state` means no period has closed yet, so any period is
    closeable. Otherwise the (stripped) `period` must compare strictly greater
    than the (stripped) prior close, treating both as opaque, lexicographically-
    ordered period labels. A period at or before the prior close is refused — the
    fail-safe direction is BLOCK, so equal labels never re-close (§5: never edit
    filed prior-period state). Reads `prior_period_state`; never mutates it.
    """
    prior = (prior_period_state or "").strip()
    if not prior:
        return (
            CloseCheck(
                CHECK_PERIOD_CLOSEABLE,
                True,
                f"No prior closed period on record (prior_period_state unset) — "
                f"period {period!r} is closeable.",
            ),
            None,
        )
    if period.strip() > prior:
        return (
            CloseCheck(
                CHECK_PERIOD_CLOSEABLE,
                True,
                f"Period {period!r} is after the last closed period {prior!r} — "
                f"closeable.",
            ),
            None,
        )
    reason = (
        f"Period {period!r} is at or before the last closed period {prior!r} — "
        f"refusing to re-close or edit a filed period (§5: never edit filed "
        f"prior-period state)."
    )
    return (
        CloseCheck(CHECK_PERIOD_CLOSEABLE, False, reason),
        CloseBlocker(CHECK_PERIOD_CLOSEABLE, reason, None),
    )


def _check_reconciliation_clean(
    reconciliation: ReconciliationReport,
) -> tuple[CloseCheck, list[CloseBlocker]]:
    """Precondition 2 — no open reconciliation gaps and no to-confirm pairs.

    Every gap and every to-confirm pair is its own blocker (each already carries
    its §5.5 reason and links back to its statement line / transaction). Category
    proposals are confirmed at sign-off, but a reconciliation discrepancy is never
    signable over — any open item here blocks.
    """
    blockers = [
        CloseBlocker(
            CHECK_RECONCILIATION_CLEAN,
            f"Open reconciliation gap ({gap.kind.value}) — {gap.reason}",
            gap,
        )
        for gap in reconciliation.gaps
    ] + [
        CloseBlocker(
            CHECK_RECONCILIATION_CLEAN,
            f"Reconciliation pair awaiting human confirm/reject — {pair.reason}",
            pair,
        )
        for pair in reconciliation.to_confirm
    ]
    if not blockers:
        check = CloseCheck(
            CHECK_RECONCILIATION_CLEAN,
            True,
            "Reconciliation is clean — no open gaps and nothing awaiting confirmation.",
        )
    else:
        check = CloseCheck(
            CHECK_RECONCILIATION_CLEAN,
            False,
            f"Reconciliation has {len(reconciliation.gaps)} open gap(s) and "
            f"{len(reconciliation.to_confirm)} pair(s) awaiting confirmation — "
            f"each must be resolved (and the pipeline re-run) before close.",
        )
    return check, blockers


def _check_categorization_complete(
    categorization: CategorizationReport,
) -> tuple[CloseCheck, list[CloseBlocker]]:
    """Precondition 3 — no un-categorizable (flagged) lines.

    Only `flagged` lines block; category **proposals do not** — the human signing
    the close confirms those (§5.7). Each flag is its own blocker, carrying the
    §5.2/§5.3 reason it could not be categorized.
    """
    blockers = [
        CloseBlocker(
            CHECK_CATEGORIZATION_COMPLETE,
            f"Un-categorized transaction — {flag.reason}",
            flag,
        )
        for flag in categorization.flagged
    ]
    if not blockers:
        check = CloseCheck(
            CHECK_CATEGORIZATION_COMPLETE,
            True,
            f"Every transaction is categorized — {len(categorization.proposals)} "
            f"proposal(s) for the human to confirm at sign-off, none flagged.",
        )
    else:
        check = CloseCheck(
            CHECK_CATEGORIZATION_COMPLETE,
            False,
            f"{len(categorization.flagged)} transaction(s) could not be "
            f"categorized — each must be categorized (and the pipeline re-run) "
            f"before close.",
        )
    return check, blockers


def _check_tax_clean(
    tax_summary: TaxSummary,
) -> tuple[CloseCheck, list[CloseBlocker]]:
    """Precondition 4 — no flagged tax exceptions; the totals were struck.

    The summary having been produced means the per-target totals were struck; any
    `flagged` exception (e.g. tax captured with no target it can be tied to, §5.3)
    blocks until resolved. Each flag is its own blocker.
    """
    blockers = [
        CloseBlocker(
            CHECK_TAX_CLEAN,
            f"Tax exception held out of the totals — {flag.reason}",
            flag,
        )
        for flag in tax_summary.flagged
    ]
    if not blockers:
        check = CloseCheck(
            CHECK_TAX_CLEAN,
            True,
            f"Tax is clean — {tax_summary.regime} totals struck "
            f"({tax_summary.period_total} for the period), nothing flagged.",
        )
    else:
        check = CloseCheck(
            CHECK_TAX_CLEAN,
            False,
            f"{len(tax_summary.flagged)} tax exception(s) held out of the totals "
            f"— each must be resolved (and the pipeline re-run) before close.",
        )
    return check, blockers


def _open_item_count(
    reconciliation: ReconciliationReport,
    tax_summary: TaxSummary,
    categorization: CategorizationReport,
) -> int:
    """Total outstanding items across the three reports (the summary's `open`).

    The same quantities the preconditions check, summed — so a `READY` close
    (every precondition met) has this at exactly 0.
    """
    return (
        len(reconciliation.gaps)
        + len(reconciliation.to_confirm)
        + len(categorization.flagged)
        + len(tax_summary.flagged)
    )


def _summarize(
    period: str,
    reconciliation: ReconciliationReport,
    tax_summary: TaxSummary,
    categorization: CategorizationReport,
) -> PeriodSummary:
    """Build the period disposition counts from the reports (counts reconcile).

    `processed` is the categorization report's per-transaction coverage (it holds
    exactly one entry per period transaction — a proposal or a flag), partitioned
    into `auto_filed` (confident proposals) and `reviewed` (flags), so
    ``processed == auto_filed + reviewed`` holds by construction. `open` is the
    cross-report outstanding tally — 0 for the `READY` close this is built for.
    """
    auto_filed = len(categorization.proposals)
    reviewed = len(categorization.flagged)
    return PeriodSummary(
        processed=auto_filed + reviewed,
        auto_filed=auto_filed,
        reviewed=reviewed,
        open=_open_item_count(reconciliation, tax_summary, categorization),
    )


# --- The skill operation ----------------------------------------------------


def close_period(
    reconciliation: ReconciliationReport,
    tax_summary: TaxSummary,
    categorization: CategorizationReport,
    config: BookkeeperConfig,
    period: str,
) -> CloseReport:
    """Assemble `period` and propose it for sign-off — proposed, never signed (§5.7).

    1. Check the period is closeable vs `config.prior_period_state` (else BLOCKED;
       never touch prior state).
    2. Run the precondition checklist over the three reports — reconciliation
       clean, categorization complete, tax clean.
    3. Any unmet → BLOCKED + every open item as a blocker; a signable close is
       never produced over an open item.
    4. All met → assemble (the period summary + the assembled period) and return
       READY.

    A pure function of the reports + config + period: it reads no source, takes no
    sink / writer / queue, and **writes nothing canonical** — it cannot mutate the
    ledger, the system of record, the queue, or `prior_period_state`, and it never
    marks the period closed/signed. The human signs.
    """
    period_check, period_blocker = _check_period_closeable(
        period, config.prior_period_state
    )
    recon_check, recon_blockers = _check_reconciliation_clean(reconciliation)
    cat_check, cat_blockers = _check_categorization_complete(categorization)
    tax_check, tax_blockers = _check_tax_clean(tax_summary)

    checklist = (period_check, recon_check, cat_check, tax_check)
    blockers: list[CloseBlocker] = []
    if period_blocker is not None:
        blockers.append(period_blocker)
    blockers.extend(recon_blockers)
    blockers.extend(cat_blockers)
    blockers.extend(tax_blockers)

    if blockers:
        # BLOCKED: never assemble a signable close over an open item (§5.7).
        return CloseReport(
            period=period,
            status=CloseStatus.BLOCKED,
            checklist=checklist,
            blockers=tuple(blockers),
            proposed_close=None,
        )

    # READY: every precondition met — assemble the proposed close. Proposed for a
    # human to sign; close_period writes nothing and never signs (§5.7).
    proposed_close = ProposedClose(
        summary=_summarize(period, reconciliation, tax_summary, categorization),
        assembled=AssembledPeriod(
            period=period,
            reconciliation=reconciliation,
            tax_summary=tax_summary,
            categorization=categorization,
        ),
    )
    return CloseReport(
        period=period,
        status=CloseStatus.READY,
        checklist=checklist,
        blockers=(),
        proposed_close=proposed_close,
    )
