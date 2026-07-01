"""`reconcileAccount` — match the captured ledger against the authoritative statement.

The §5.5 skill, alongside `track_tax` and `categorize` on the §5 core. It reads
a period of stored transactions (via `LedgerSource`) and the authoritative bank /
card statement for the same period (via the new `StatementSource` port), matches
each captured transaction one-to-one against a statement line, sorts every line
into exactly one bucket, and returns a `ReconciliationReport` of matched pairs,
pairs that need human confirmation, and gaps.

The §5 boundary, preserved exactly (§5.5):

- **Detection-only — mutates nothing.** Reconcile reads two sources and *returns
  a report*. It writes nothing to the ledger, the statement, the system of
  record, or anywhere canonical — there is no sink, no statement writer, no
  system-of-record handle in this module, so it *cannot* publish. Because it
  mutates nothing it is not even an autonomy question. A test pins it writes
  nothing canonical.
- **Resolution is always human.** The skill never auto-adjusts, auto-matches-away,
  or "fixes" a gap, and never silently accepts a doubtful link — it only
  *surfaces*. Routing the gaps and the confirm-tier to the `ReviewQueue` (or any
  review surface) is a later, gated step; this skill just produces the report.
- **Surface every gap, however small — no materiality filter.** The charter is
  explicit that the agent never silently reconciles a mismatch *however small*,
  so `config.materiality_floor` is **not** consulted here (that floor governs
  advisory anomalies in `flagAnomaly`, not reconcile). A one-cent amount mismatch
  is surfaced exactly like a thousand-dollar one. A test proves a tiny gap is
  still reported.

**The match — three buckets, completeness invariant.** Every ledger transaction
and every statement line lands in **exactly one** of `{matched, to_confirm, gap}`:

1. **Exact pass — link on amount + date.** Pair a transaction with a statement
   line on **exact Decimal amount equality** *and* a **date within
   `config.reconcile_date_window()`** (statements post on a delay, so an
   exact-date rule would manufacture gaps; the window default is documented on
   the config accessor). When several still-free candidates share an amount and a
   date, the **normalized vendor similarity** disambiguates; ties break on ledger
   read order. Amount+date is kept as the link even when the vendors differ —
   bank descriptors are mangled, so *requiring* vendor agreement here would
   manufacture false gaps. The **vendor similarity then decides the tier**:
   - **≥ the `reconcile_vendor` floor → confident `matched`** (kept for the trail).
   - **< the floor (or the boundary is inert) → `to_confirm`:** the pair is
     *linked* (not a false gap) but surfaced for a human to confirm or reject —
     **never silently matched**. This closes the hole where a coincidental
     same-amount / same-date charge from an *unrelated* vendor would otherwise be
     absorbed as a clean match, burying a genuine missing entry + duplicate.
2. **Amount-mismatch pass.** Among what pass 1 left over, pair a transaction with
   a statement line whose **date is within the window** and whose **vendor agrees**
   (normalized similarity ≥ `_VENDOR_AGREEMENT`) but whose **amounts differ**.
   Amount can't be the discriminator here, so vendor evidence must be strong.
   These become `AMOUNT_MISMATCH` gaps, carrying both amounts and the signed
   `delta`.

Whatever is still unpaired after both passes is a one-sided gap: a statement line
with no transaction is `UNMATCHED_IN_LEDGER` (a charge the books never captured);
a transaction with no statement line is `UNMATCHED_ON_STATEMENT` (captured but
the authoritative feed does not show it — a duplicate, an error, or timing).

**The `reconcile_vendor` floor is a §5 boundary** (it governs silent-accept vs
surface), so it is wired like the other skills' thresholds: a
`confidence_thresholds["reconcile_vendor"]` key, **inert until configured**. Unset
→ the skill leans toward surfacing (every amount+date pair becomes `to_confirm`,
nothing auto-matched), so no instance silently auto-confirms a divergent pair
before its boundary is set. The documented, conservative recommended value to
configure once a live feed exists is `config.DEFAULT_RECONCILE_VENDOR_FLOOR`; it
should be calibrated against real mangled-descriptor data, but a sane default
suffices for now.

**Descriptor normalization (recall, dependency-free).** A statement descriptor is
mangled — a payment-processor prefix (`SQ *`, `TST*`, `PP*`, `POS DEBIT`) and a
trailing store / location code wrap the real merchant name. `_normalize_descriptor`
strips those and the punctuation before the compare, so a genuine
mangled-but-same vendor (`SQ *JOE'S CAFE 415` vs `Joe's Cafe`) scores high and
auto-confirms — no false-gap noise — while a truly different vendor stays low and
surfaces. The fuzzy compare is stdlib `difflib` (like `categorize`): the core
stays dependency-free.

**Decimal money, exact.** Amount matching is exact `Decimal` equality — money is
`Decimal` everywhere (post-#9), so a difference is a *real* discrepancy, never a
float-rounding artifact, and the mismatch `delta` is itself an exact `Decimal`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from difflib import SequenceMatcher
from enum import Enum

from bookkeeper.config import BookkeeperConfig
from bookkeeper.model import StatementLine, Transaction
from bookkeeper.ports import LedgerSource, StatementSource

# A still-free candidate qualifies as an amount-mismatch (same charge, wrong
# amount) only when its normalized vendor similarity meets this floor. The amount
# already differs, so vendor is the only evidence the two are the same charge —
# the floor keeps unrelated same-date lines from being paired as "a mismatch".
# This is an internal pairing heuristic, not a §5 boundary: an amount mismatch is
# a surfaced gap either way (paired, or two one-sided gaps), so nothing is ever
# silently accepted here — unlike the `reconcile_vendor` floor of pass 1.
_VENDOR_AGREEMENT = 0.6

# Card / payment-processor descriptor prefixes that a statement prepends to the
# real merchant name (Square, Toast, PayPal, generic point-of-sale). Stripped
# before the vendor compare so a mangled statement descriptor still scores
# against the clean ledger vendor. Generic processor tokens — not client-,
# bank-, or vendor-specific. Ordered longest-first so a specific prefix is
# stripped before a shorter one it contains.
_PROCESSOR_PREFIXES = (
    "pos debit ",
    "pos debit",
    "sq *",
    "sq*",
    "tst* ",
    "tst*",
    "pp* ",
    "pp*",
    "pos ",
)

# Split a descriptor on any run of non-alphanumeric characters (spaces,
# punctuation, the processor '*').
_NON_ALNUM = re.compile(r"[^0-9a-z]+")


# --- The result model (detection-only, traceable) ---------------------------


class GapKind(str, Enum):
    """Which reconciliation gap bucket a discrepancy falls in (exactly one).

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
class PairToConfirm:
    """An amount+date pair whose vendors diverge too much to accept silently.

    The confidence tier between `matched` and a gap: the transaction and statement
    line agree on amount and date (so they are very likely the same charge — not a
    false gap), but their vendor descriptors are too dissimilar to accept
    silently, so the pair is *linked and surfaced* for a human to confirm or
    reject (§5.5: never silently reconcile a divergent-vendor collision). Carries
    the linked `pair`, the `vendor_similarity` that fell below the floor (or was
    not evaluated because the boundary is inert), and the §1-traceable `reason`.
    Detection-only: reported, never auto-resolved.
    """

    pair: MatchedPair
    vendor_similarity: float
    reason: str


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
    the ledger, the statement, or the system of record. Every ledger transaction
    and every statement line lands in exactly one of the three buckets:

    - `matched` — confident reconciled pairs (the trail of what reconciled);
    - `to_confirm` — amount+date pairs whose vendors diverge, linked but surfaced
      for human confirm/reject (never silently matched);
    - `gaps` — what did not reconcile at all, surfaced for human resolution.

    Ordering is deterministic: `matched` and `to_confirm` in statement read order;
    `gaps` grouped by kind — `AMOUNT_MISMATCH`, `UNMATCHED_IN_LEDGER` (both
    statement read order), then `UNMATCHED_ON_STATEMENT` (ledger read order).
    """

    period: str
    matched: tuple[MatchedPair, ...]
    to_confirm: tuple[PairToConfirm, ...]
    gaps: tuple[ReconciliationGap, ...]


# --- The matcher (pure, deterministic) --------------------------------------


def _normalize_descriptor(text: str) -> str:
    """Normalize a vendor / statement descriptor for fuzzy comparison.

    Lowercases, strips a leading payment-processor prefix (`SQ *`, `TST*`, `PP*`,
    `POS DEBIT`), splits off punctuation, and drops pure-digit tokens (store
    numbers, location / reference codes), so a mangled statement descriptor
    (``SQ *JOE'S CAFE 415``) reduces to the same words as the clean ledger vendor
    (``Joe's Cafe``) — both → ``"joe s cafe"``. Stdlib only; the core stays
    dependency-free.
    """
    s = (text or "").lower().strip()
    for prefix in _PROCESSOR_PREFIXES:
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    tokens = [t for t in _NON_ALNUM.split(s) if t and not t.isdigit()]
    return " ".join(tokens)


def _vendor_similarity(transaction: Transaction, line: StatementLine) -> float:
    """Normalized fuzzy similarity of a transaction's vendor to a statement line.

    `difflib` ratio over the two normalized descriptors (`_normalize_descriptor`)
    — used both to disambiguate among equal amount+date candidates and as the
    confidence that sorts a linked pair into confident `matched` vs surfaced
    `to_confirm`.
    """
    return SequenceMatcher(
        None,
        _normalize_descriptor(transaction.vendor),
        _normalize_descriptor(line.description),
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


def _confirm_reason(vendor_floor: float | None, score: float) -> str:
    """The §1-traceable why an amount+date pair was surfaced rather than matched."""
    if vendor_floor is None:
        return (
            "Reconcile vendor-confirm boundary not configured (inert) — the "
            "amount+date pair is linked but surfaced for human confirmation rather "
            "than silently matched (§5: inert until configured)."
        )
    return (
        f"Amount and date agree but vendor similarity {score:.2f} is below the "
        f"reconcile_vendor floor {vendor_floor:.2f} — linked but surfaced for human "
        f"confirm/reject rather than silently matched (§5.5: never silently "
        f"reconcile a divergent-vendor collision)."
    )


def _reconcile(
    transactions: list[Transaction],
    statement_lines: list[StatementLine],
    window_days: int,
    vendor_floor: float | None,
) -> tuple[list[MatchedPair], list[PairToConfirm], list[ReconciliationGap]]:
    """Match one-to-one and sort every line into matched / to_confirm / gap.

    Two passes over the leftovers (exact-amount link, then amount-mismatch), then
    one-sided leftovers become gaps. One-to-one is enforced by marking each side
    consumed; every choice is deterministic (best vendor similarity, ties to
    earliest ledger read order). No size/materiality filter anywhere — every gap
    is kept, however small.
    """
    txn_used = [False] * len(transactions)
    line_used = [False] * len(statement_lines)

    matched: list[MatchedPair] = []
    to_confirm: list[PairToConfirm] = []

    # Pass 1 — link on exact amount + date-window (recall-preserving: bank
    # descriptors are mangled, so requiring vendor agreement to *link* would
    # manufacture false gaps). When several free candidates share the amount and
    # date, vendor similarity disambiguates which to link; that same similarity
    # then sorts the linked pair into confident `matched` (>= floor) vs surfaced
    # `to_confirm` (< floor, or the boundary is inert) — never silently matched.
    for li, line in enumerate(statement_lines):
        best_ti: int | None = None
        best_score = -1.0
        for ti, txn in enumerate(transactions):
            if txn_used[ti] or txn.amount != line.amount:
                continue
            if not _within_window(txn, line, window_days):
                continue
            score = _vendor_similarity(txn, line)
            if score > best_score:  # strict > → ties keep the earliest ledger txn
                best_score = score
                best_ti = ti
        if best_ti is None:
            continue
        txn_used[best_ti] = True
        line_used[li] = True
        pair = MatchedPair(transactions[best_ti], line)
        if vendor_floor is not None and best_score >= vendor_floor:
            matched.append(pair)
        else:
            to_confirm.append(
                PairToConfirm(pair, best_score, _confirm_reason(vendor_floor, best_score))
            )

    # Pass 2 — amount mismatch: date-window + vendor agrees, amount differs. The
    # amount can't discriminate (it differs by definition), so the vendor must
    # clear `_VENDOR_AGREEMENT` for the two to be called the same charge.
    amount_mismatches: list[ReconciliationGap] = []
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
    return matched, to_confirm, gaps


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
    2. Link one-to-one: exact (amount-exact + date-window, vendor disambiguates),
       then amount-mismatch (date-window + vendor agrees, amount differs).
    3. Sort every line into `matched` (vendor ≥ the `reconcile_vendor` floor),
       `to_confirm` (linked amount+date pair, vendor below floor or boundary
       inert — surfaced, never silently matched), or a gap — **no materiality
       filter; every gap surfaced however small** (§5.5).
    4. Return the `ReconciliationReport` — detection-only, **writes nothing
       canonical**; confirming a pair or resolving a gap is a later, human-gated
       step.

    The only ledger-touching argument is a read-side `LedgerSource` and the only
    statement-touching argument is a read-side `StatementSource` — there is no
    writer of any kind, so the skill cannot mutate. `config` supplies the
    date-match window and the `reconcile_vendor` confirm floor (inert until
    configured → lean toward surfacing); `materiality_floor` is deliberately never
    read here.
    """
    window_days = config.reconcile_date_window()
    vendor_floor = config.reconcile_vendor_threshold()

    transactions = await ledger_source.fetch_for_period(period)
    statement_lines = await statement_source.fetch_statement(period)

    matched, to_confirm, gaps = _reconcile(
        transactions, statement_lines, window_days, vendor_floor
    )

    return ReconciliationReport(
        period=period,
        matched=tuple(matched),
        to_confirm=tuple(to_confirm),
        gaps=tuple(gaps),
    )
