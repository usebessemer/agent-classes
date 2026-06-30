"""`reconcileAccount` — match the captured ledger against the authoritative statement.

The §5.5 skill, alongside `track_tax` and `categorize` on the §5 core. It reads
a period of stored transactions (via `LedgerSource`) and the authoritative bank /
card statement for the same period (via the new `StatementSource` port), matches
each captured transaction one-to-one against a statement line, classifies every
line into exactly one bucket, and returns a `ReconciliationReport` of matched
pairs plus every gap.

The §5 boundary, preserved exactly (§5.5):

- **Detection-only — mutates nothing.** Reconcile reads two sources and *returns
  a report*. It writes nothing to the ledger, the statement, the system of
  record, or anywhere canonical — there is no sink, no statement writer, no
  system-of-record handle in this module, so it *cannot* publish. Because it
  mutates nothing it is not even an autonomy question. A test pins it writes
  nothing canonical.
- **Resolution is always human.** The skill never auto-adjusts, auto-matches-away,
  or "fixes" a gap — it only *surfaces* it. Routing the gaps to the `ReviewQueue`
  (or any review surface) is a later, gated step; this skill just produces the
  report.
- **Surface every gap, however small — no materiality filter.** The charter is
  explicit that the agent never silently reconciles a mismatch *however small*,
  so `config.materiality_floor` is **not** consulted here (that floor governs
  advisory anomalies in `flagAnomaly`, not reconcile). A one-cent amount mismatch
  is surfaced exactly like a thousand-dollar one. A test proves a tiny gap is
  still reported.

**The match (one-to-one), in two passes over the leftovers:**

1. **Exact pass** — pair a transaction with a statement line on **exact Decimal
   amount equality** *and* a **date within `config.reconcile_date_window()`**
   (statements post on a delay, so an exact-date rule would manufacture gaps; the
   window default is documented on the config accessor). When several still-free
   candidates share an amount and a date, the **vendor fuzzy similarity**
   (stdlib `difflib`, like `categorize` — the core stays dependency-free)
   disambiguates; ties break on ledger read order, so the result is deterministic.
   These become `matched` pairs (kept in the report for the trail).
2. **Amount-mismatch pass** — among what the exact pass left over, pair a
   transaction with a statement line whose **date is within the window** and whose
   **vendor agrees** (fuzzy similarity at or above `_VENDOR_AGREEMENT`) but whose
   **amounts differ**. Amount can't be the discriminator here, so vendor evidence
   must be strong. These become `AMOUNT_MISMATCH` gaps, carrying both amounts and
   the signed `delta`.

Whatever is still unpaired after both passes is a one-sided gap: a statement line
with no transaction is `UNMATCHED_IN_LEDGER` (a charge the books never captured);
a transaction with no statement line is `UNMATCHED_ON_STATEMENT` (captured but
the authoritative feed does not show it — a duplicate, an error, or timing).

**Decimal money, exact.** Amount matching is exact `Decimal` equality — money is
`Decimal` everywhere (post-#9), so a difference is a *real* discrepancy, never a
float-rounding artifact, and the mismatch `delta` is itself an exact `Decimal`.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from difflib import SequenceMatcher
from enum import Enum

from bookkeeper.config import BookkeeperConfig
from bookkeeper.model import StatementLine, Transaction
from bookkeeper.ports import LedgerSource, StatementSource

# A still-free candidate qualifies as an amount-mismatch (same charge, wrong
# amount) only when its vendor fuzzy similarity meets this floor. The amount
# already differs, so vendor is the only evidence the two are the same charge —
# the floor keeps unrelated same-date lines from being paired as "a mismatch".
_VENDOR_AGREEMENT = 0.6


# --- The result model (proposed / detection-only, traceable) ----------------


class GapKind(str, Enum):
    """Which reconciliation bucket a gap falls in (every gap is exactly one).

    A `str` enum (like `RunOutcome`) so the kind serializes to a stable, readable
    tag for the run log and any later review surface.
    """

    #: A statement line with no matching ledger transaction (the books missed it).
    UNMATCHED_IN_LEDGER = "unmatched_in_ledger"
    #: A ledger transaction with no matching statement line (not on the feed).
    UNMATCHED_ON_STATEMENT = "unmatched_on_statement"
    #: A transaction and a line agree on date + vendor but the amounts differ.
    AMOUNT_MISMATCH = "amount_mismatch"


@dataclass(frozen=True)
class MatchedPair:
    """A ledger transaction and the statement line it reconciled against.

    Kept in the report for the trail (charter §1: fully traceable) — the evidence
    of what *did* reconcile, not just what did not. The `statement_line`'s
    `statement_ref` links the pair back to the authoritative feed.
    """

    transaction: Transaction
    statement_line: StatementLine


@dataclass(frozen=True)
class ReconciliationGap:
    """One surfaced discrepancy between the books and the authoritative statement.

    `kind` is the bucket; `reason` is the human-readable §1-traceable why. The
    side(s) present depend on the kind: an `AMOUNT_MISMATCH` carries both the
    `transaction` and the `statement_line` plus the signed `delta`; a one-sided
    gap carries only the side that exists. Detection-only — a gap is *surfaced*
    for human resolution, never auto-fixed (§5.5).
    """

    kind: GapKind
    reason: str
    transaction: Transaction | None = None
    statement_line: StatementLine | None = None
    #: For `AMOUNT_MISMATCH`: `transaction.amount - statement_line.amount` (signed,
    #: exact `Decimal`). `None` for the one-sided gap kinds.
    delta: Decimal | None = None


@dataclass(frozen=True)
class ReconciliationReport:
    """A **detection-only** reconciliation of one period (charter `reconcileAccount`).

    Never published (§5.5): `reconcile_account` returns this; it writes nothing to
    the ledger, the statement, or the system of record. Carries the `matched`
    pairs (the trail of what reconciled) and every `gap` (what did not), each
    surfaced for human resolution. Ordering is deterministic: `matched` in
    statement read order; `gaps` grouped by kind — `AMOUNT_MISMATCH`,
    `UNMATCHED_IN_LEDGER` (both statement read order), then
    `UNMATCHED_ON_STATEMENT` (ledger read order).
    """

    period: str
    matched: tuple[MatchedPair, ...]
    gaps: tuple[ReconciliationGap, ...]


# --- The matcher (pure, deterministic) --------------------------------------


def _normalize(text: str) -> str:
    """Lowercase and collapse whitespace for fuzzy vendor comparison."""
    return " ".join((text or "").lower().split())


def _vendor_similarity(transaction: Transaction, line: StatementLine) -> float:
    """Fuzzy similarity of a transaction's vendor to a statement line's description.

    Stdlib `difflib` ratio over the normalized strings (the core stays
    dependency-free, as in `categorize`). Used to disambiguate among exact
    amount+date candidates, and as the agreement signal for an amount mismatch.
    """
    return SequenceMatcher(
        None, _normalize(transaction.vendor), _normalize(line.description)
    ).ratio()


def _within_window(
    transaction: Transaction, line: StatementLine, window_days: int
) -> bool:
    """Whether the two fall within `window_days` calendar days of each other.

    Compares calendar dates (`.date()`) so a posting that lands a day or two late
    still pairs; comparing whole days avoids a sub-day timestamp difference being
    mis-signed by `timedelta.days` flooring.
    """
    return abs((transaction.date.date() - line.date.date()).days) <= window_days


def _reconcile(
    transactions: list[Transaction],
    statement_lines: list[StatementLine],
    window_days: int,
) -> tuple[list[MatchedPair], list[ReconciliationGap]]:
    """Match one-to-one and classify every line — the pure heart of the skill.

    Two passes over the leftovers (exact, then amount-mismatch), then one-sided
    leftovers become gaps. One-to-one is enforced by marking each side consumed;
    every choice is deterministic (best vendor similarity, ties to earliest
    ledger read order). No size/materiality filter anywhere — every gap is kept.
    """
    txn_used = [False] * len(transactions)
    line_used = [False] * len(statement_lines)

    matched: list[MatchedPair] = []

    # Pass 1 — exact: amount-exact + date-window. Vendor only disambiguates when
    # several free candidates share the amount and date (an exact amount+date hit
    # is a confident match on its own, so no vendor floor is required here).
    for li, line in enumerate(statement_lines):
        best_ti: int | None = None
        best_score = -1.0
        for ti, txn in enumerate(transactions):
            if txn_used[ti]:
                continue
            if txn.amount != line.amount:
                continue
            if not _within_window(txn, line, window_days):
                continue
            score = _vendor_similarity(txn, line)
            if score > best_score:  # strict > → ties keep the earliest ledger txn
                best_score = score
                best_ti = ti
        if best_ti is not None:
            txn_used[best_ti] = True
            line_used[li] = True
            matched.append(MatchedPair(transactions[best_ti], line))

    amount_mismatches: list[ReconciliationGap] = []

    # Pass 2 — amount mismatch: date-window + vendor agrees, amount differs. The
    # amount can't discriminate (it differs by definition), so the vendor must
    # clear `_VENDOR_AGREEMENT` for the two to be called the same charge.
    for li, line in enumerate(statement_lines):
        if line_used[li]:
            continue
        best_ti = None
        best_score = -1.0
        for ti, txn in enumerate(transactions):
            if txn_used[ti] or txn.amount == line.amount:
                continue
            if not _within_window(txn, line, window_days):
                continue
            score = _vendor_similarity(txn, line)
            if score >= _VENDOR_AGREEMENT and score > best_score:
                best_score = score
                best_ti = ti
        if best_ti is not None:
            txn = transactions[best_ti]
            txn_used[best_ti] = True
            line_used[li] = True
            delta = txn.amount - line.amount
            amount_mismatches.append(
                ReconciliationGap(
                    kind=GapKind.AMOUNT_MISMATCH,
                    reason=(
                        f"Ledger txn ({txn.vendor!r}, {txn.amount}) and statement "
                        f"line {line.statement_ref!r} ({line.amount}) agree on date "
                        f"and vendor but the amounts differ by {delta} — a real "
                        f"discrepancy surfaced for human resolution (§5.5: never "
                        f"auto-reconciled, however small)."
                    ),
                    transaction=txn,
                    statement_line=line,
                    delta=delta,
                )
            )

    # Leftovers — one-sided gaps. Statement lines first (in read order), then
    # ledger transactions (in read order), so the report ordering is stable.
    unmatched_in_ledger = [
        ReconciliationGap(
            kind=GapKind.UNMATCHED_IN_LEDGER,
            reason=(
                f"Statement line {line.statement_ref!r} ({line.amount}, "
                f"{line.date.date()}) has no matching ledger transaction — a charge "
                f"the books never captured. Surfaced for human resolution (§5.5)."
            ),
            statement_line=line,
        )
        for li, line in enumerate(statement_lines)
        if not line_used[li]
    ]
    unmatched_on_statement = [
        ReconciliationGap(
            kind=GapKind.UNMATCHED_ON_STATEMENT,
            reason=(
                f"Ledger transaction ({txn.vendor!r}, {txn.amount}, "
                f"{txn.date.date()}) has no matching statement line — captured but "
                f"absent from the authoritative statement (a duplicate, an error, or "
                f"timing). Surfaced for human resolution (§5.5)."
            ),
            transaction=txn,
        )
        for ti, txn in enumerate(transactions)
        if not txn_used[ti]
    ]

    gaps = amount_mismatches + unmatched_in_ledger + unmatched_on_statement
    return matched, gaps


# --- The skill operation ----------------------------------------------------


async def reconcile_account(
    ledger_source: LedgerSource,
    statement_source: StatementSource,
    config: BookkeeperConfig,
    period: str,
) -> ReconciliationReport:
    """Reconcile `period`'s books against the authoritative statement — detection only.

    1. `ledger_source.fetch_for_period(period)` + `statement_source.fetch_statement(
       period)` — read both sides, write nothing.
    2. Match one-to-one: exact (amount-exact + date-window, vendor disambiguates),
       then amount-mismatch (date-window + vendor agrees, amount differs).
    3. Classify every line into matched / the three gap kinds — **no materiality
       filter; every gap is surfaced however small** (§5.5).
    4. Return the `ReconciliationReport` — detection-only, **writes nothing
       canonical**; resolving any gap is a later, human-gated step.

    The only ledger-touching argument is a read-side `LedgerSource` and the only
    statement-touching argument is a read-side `StatementSource` — there is no
    writer of any kind, so the skill cannot mutate. `config` supplies only the
    date-match window; `materiality_floor` is deliberately never read here.
    """
    window_days = config.reconcile_date_window()

    transactions = await ledger_source.fetch_for_period(period)
    statement_lines = await statement_source.fetch_statement(period)

    matched, gaps = _reconcile(transactions, statement_lines, window_days)

    return ReconciliationReport(
        period=period,
        matched=tuple(matched),
        gaps=tuple(gaps),
    )
