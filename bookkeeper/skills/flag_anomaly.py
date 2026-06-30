"""`flagAnomaly` — surface MECHANICAL anomalies in a period for human attention.

The advisory skill on the §5 core, alongside the detection-only `reconcileAccount`
and the proposing computation skills. It reads a period of stored transactions
(via `LedgerSource`) and scans each **individual record** for *mechanical*
defects, returning an `AnomalyReport` of flags. It is the lowest-risk skill in the
class: it **never acts, never mutates, never blocks a downstream skill, and writes
nothing** — it only surfaces.

The §5 boundary, preserved exactly:

- **Advisory / never-acts / writes-nothing.** `flag_anomaly` *returns* a report.
  Its only ledger-touching argument is a read-side `LedgerSource` — there is no
  sink, no writer, no system-of-record handle in this module, so it *cannot*
  publish. It mutates nothing, so it is not even an autonomy question; a test pins
  it writes nothing canonical. It also never blocks another skill: a flag is a
  note for a human, not a gate — `closePeriod`/`reconcileAccount` run regardless.
- **§5.6 — over-materiality is surfaced even when confidently attributed.** A
  large item is flagged for a human to eyeball purely on its size, regardless of
  how confidently it was attributed or categorized.

🚧 **THE LINE — §2, mechanical-only (this is the skill most likely to bloat).**
`flagAnomaly` looks at *individual records for mechanical defects* — never at
**trends across records**. There is deliberately **no trend / forecast / pattern /
predictive modelling, no spend analytics, no "this job is trending over budget".**
That inferential, history-based early-warning layer is **excluded by charter §2**
(the analytics layer; specifically the scoped-out predictive early-warning). Every
check below decides from one record's own fields (or, for duplicates, from the
literal equality of a handful of records) — none consults history, builds a model,
or projects a trend. If a check needed history or modelling to decide, it would be
out of scope. A test pins that a clean-but-"trending" period produces no flags.

**The three mechanical checks (and only these):**

1. **Duplicates** — the same charge captured more than once: same vendor (lightly
   normalized — whitespace + case only, since ledger vendors are already clean,
   *not* the processor-prefix stripping `reconcileAccount` needs for mangled bank
   descriptors) **and** the same exact `Decimal` amount **and** dates within a
   small window (`_DUPLICATE_WINDOW_DAYS`). All members of a duplicate group are
   flagged together. Beyond the window the same vendor+amount is far more likely a
   genuine recurring charge (a weekly / monthly bill) than a double-capture, so it
   is **not** flagged — and crucially the skill never *infers* a recurrence cadence,
   it simply declines to pair records more than the window apart (still mechanical).
2. **Over-materiality** (§5.6) — `abs(amount)` strictly over `config.materiality_floor`:
   a large item to eyeball even when confidently attributed. **Inert until
   configured:** an unset (`None`) floor skips this check entirely (no threshold,
   nothing to compare) while duplicates + malformed still scan, since they need no
   threshold.
3. **Malformed / incomplete** — structural defects in a single record: a missing
   vendor, a missing date, or a zero / absent amount where a captured charge is
   expected to carry a magnitude. (The source-artifact link is deliberately **not**
   checked: the `LedgerSource` read path legitimately projects `artifact_bytes`
   away — `b""` — so emptiness here is the read-path contract, not a defect.) One
   flag per malformed record, its `reason` enumerating every defect found.

**Decimal money, exact.** Amounts are `Decimal` at the model (exact currency), so
the over-materiality comparison is exact and duplicate amount-equality is a true
match, never a float-rounding artifact. `config.materiality_floor` is itself
`Decimal` (coerced in the config), so the comparison never mixes `Decimal` and
`float`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum

from bookkeeper.config import BookkeeperConfig
from bookkeeper.model import Transaction
from bookkeeper.ports import LedgerSource

# Decimal zero, reused for the zero-amount malformed check (never coerced to float).
_ZERO = Decimal("0")

# The ± day window two same-vendor, same-amount records must fall within to be
# treated as a likely double-capture (a duplicate). Kept deliberately small: a
# duplicate is the *same* charge captured twice (same or adjacent day from a
# capture-timing lag), whereas the same vendor+amount a week or a month apart is
# almost certainly a genuine recurring charge. This is a mechanical proximity
# tolerance, **not** a recurrence/trend inference (§2): beyond the window the skill
# simply does not pair the records — it never models a cadence.
_DUPLICATE_WINDOW_DAYS = 1


# --- The result model (advisory, traceable) ---------------------------------


class AnomalyKind(str, Enum):
    """Which mechanical-anomaly bucket a flag falls in (exactly one).

    A `str` enum (like `GapKind` / `RunOutcome`) so the kind serializes to a
    stable, readable tag for the run log and any later review surface. The three
    kinds are the *only* checks the skill performs — there is no inferential /
    trend / forecast kind by design (§2).
    """

    #: The same charge captured more than once (same vendor + amount + near date).
    DUPLICATE = "duplicate"
    #: `abs(amount)` over `config.materiality_floor` — a large item to eyeball (§5.6).
    OVER_MATERIALITY = "over_materiality"
    #: A structural defect in one record (missing vendor / date, zero / absent amount).
    MALFORMED = "malformed"


@dataclass(frozen=True)
class AnomalyFlag:
    """One surfaced mechanical anomaly — advisory, never acted on.

    `kind` is the bucket; `reason` is the human-readable §1-traceable why.
    `transactions` holds the implicated record(s): **exactly one** for
    `OVER_MATERIALITY` and `MALFORMED`; the **full duplicate group** (two or more,
    in ledger read order) for `DUPLICATE` — so "flag both/all" is satisfied by the
    group travelling together. Each transaction links back to its source via the
    ledger (charter §1: fully traceable). A flag is a note for a human, never a
    gate: the skill mutates nothing and blocks nothing.
    """

    kind: AnomalyKind
    reason: str
    transactions: tuple[Transaction, ...]


@dataclass(frozen=True)
class AnomalyReport:
    """An **advisory** scan of one period for mechanical anomalies (charter `flagAnomaly`).

    Writes nothing (§5): `flag_anomaly` returns this; it stores nothing to the
    ledger, the system of record, or anywhere canonical, and never blocks a
    downstream skill. Carries the period and the deterministic `flags` tuple,
    grouped by kind in a fixed order — `DUPLICATE`, then `OVER_MATERIALITY`, then
    `MALFORMED` — and, within each kind, in ledger read order (a duplicate group
    orders by its earliest member). A record may legitimately appear in more than
    one flag (e.g. a malformed record that is also a duplicate): the checks are
    orthogonal and each surfaces independently.
    """

    period: str
    flags: tuple[AnomalyFlag, ...]


# --- The mechanical checks (pure, deterministic) ----------------------------


def _normalize_vendor(vendor: str | None) -> str:
    """Light, mechanical normalization of a ledger vendor for duplicate keying.

    Collapses surrounding / internal whitespace and casefolds, so ``"Acme  Supplies"``
    and ``"acme supplies"`` key together as the same vendor. Deliberately *not* the
    processor-prefix stripping `reconcileAccount` applies to mangled bank
    descriptors — ledger vendors are already clean, and heavier inference is out of
    this skill's mechanical scope.
    """
    return " ".join((vendor or "").split()).casefold()


def _is_blank(text: str | None) -> bool:
    """Whether a string field is missing / blank (None, empty, or whitespace-only)."""
    return not (text or "").strip()


def _within_days(a: datetime, b: datetime, window: int) -> bool:
    """Whether two timestamps fall within `window` calendar days of each other.

    Compares calendar dates (`.date()`) so a sub-day timestamp difference is never
    mis-signed by `timedelta.days` flooring (mirrors `reconcileAccount`).
    """
    return abs((a.date() - b.date()).days) <= window


def _duplicate_flags(transactions: list[Transaction]) -> list[AnomalyFlag]:
    """Flag groups of likely double-captured records (same vendor + amount + near date).

    Buckets records by `(normalized_vendor, exact Decimal amount)`, then within
    each bucket clusters by date proximity: the earliest-read record anchors a
    group and every still-free record within `_DUPLICATE_WINDOW_DAYS` of that
    anchor joins it; a group of two or more is a duplicate. Records with an absent
    amount or date can't establish the key/proximity and are left to the malformed
    check. Deterministic: groups are emitted ordered by their earliest member's
    ledger read index.
    """
    # Bucket read-order indices by (vendor, amount). A record with no amount has no
    # comparable key — it is the malformed check's concern, not a duplicate.
    buckets: dict[tuple[str, Decimal], list[int]] = {}
    for i, txn in enumerate(transactions):
        if txn.amount is None:
            continue
        buckets.setdefault((_normalize_vendor(txn.vendor), txn.amount), []).append(i)

    groups: list[list[int]] = []
    for indices in buckets.values():  # dict preserves first-seen (read) order
        if len(indices) < 2:
            continue
        remaining = list(indices)
        while remaining:
            anchor = remaining[0]
            anchor_date = transactions[anchor].date
            group = [anchor]
            rest: list[int] = []
            for j in remaining[1:]:
                other_date = transactions[j].date
                if (
                    anchor_date is not None
                    and other_date is not None
                    and _within_days(anchor_date, other_date, _DUPLICATE_WINDOW_DAYS)
                ):
                    group.append(j)
                else:
                    rest.append(j)
            if len(group) >= 2:
                groups.append(group)
            remaining = rest

    groups.sort(key=min)  # global read order by earliest member
    flags: list[AnomalyFlag] = []
    for group in groups:
        members = tuple(transactions[i] for i in group)
        first = members[0]
        flags.append(
            AnomalyFlag(
                kind=AnomalyKind.DUPLICATE,
                reason=(
                    f"{len(members)} records share vendor {first.vendor!r}, amount "
                    f"{first.amount}, and a date within {_DUPLICATE_WINDOW_DAYS} day(s) "
                    f"— likely the same charge captured more than once. Surfaced for a "
                    f"human to confirm and de-duplicate (advisory; never auto-removed)."
                ),
                transactions=members,
            )
        )
    return flags


def _over_materiality_flags(
    transactions: list[Transaction], floor: Decimal | None
) -> list[AnomalyFlag]:
    """Flag each record whose `abs(amount)` is strictly over the materiality floor (§5.6).

    **Inert until configured:** an unset floor (`None`) skips the check entirely —
    there is no threshold to compare against — so nothing is flagged on size until
    a deployment sets `materiality_floor`. When set, a large item is surfaced for a
    human to eyeball *regardless of attribution confidence* (§5.6). Records with an
    absent amount are the malformed check's concern, not over-materiality.
    """
    if floor is None:
        return []
    return [
        AnomalyFlag(
            kind=AnomalyKind.OVER_MATERIALITY,
            reason=(
                f"Transaction ({txn.vendor!r}, {txn.amount}) exceeds the materiality "
                f"floor {floor} — a large item surfaced for a human to eyeball even if "
                f"confidently attributed (§5.6). Advisory; nothing is changed."
            ),
            transactions=(txn,),
        )
        for txn in transactions
        if txn.amount is not None and abs(txn.amount) > floor
    ]


def _malformed_flags(transactions: list[Transaction]) -> list[AnomalyFlag]:
    """Flag records with a structural defect (missing vendor / date, zero / absent amount).

    Inspects one record's own fields only. The source-artifact link
    (`artifact_bytes`) is deliberately **not** checked: the `LedgerSource` read path
    legitimately projects it away (`b""`), so its absence here is the read-path
    contract, not a defect — checking it would false-flag every well-formed read.
    One flag per malformed record, its `reason` enumerating every defect found.
    """
    flags: list[AnomalyFlag] = []
    for txn in transactions:
        defects: list[str] = []
        if _is_blank(txn.vendor):
            defects.append("missing vendor")
        if txn.date is None:
            defects.append("missing date")
        if txn.amount is None:
            defects.append("absent amount")
        elif txn.amount == _ZERO:
            defects.append("zero amount where a magnitude is expected")
        if defects:
            flags.append(
                AnomalyFlag(
                    kind=AnomalyKind.MALFORMED,
                    reason=(
                        f"Structural defect(s): {', '.join(defects)} — an incomplete / "
                        f"malformed record surfaced for human attention (advisory; the "
                        f"record is left exactly as captured)."
                    ),
                    transactions=(txn,),
                )
            )
    return flags


# --- The skill operation ----------------------------------------------------


async def flag_anomaly(
    ledger_source: LedgerSource,
    config: BookkeeperConfig,
    period: str,
) -> AnomalyReport:
    """Scan `period` for mechanical anomalies — advisory, writes nothing.

    1. `ledger_source.fetch_for_period(period)` → the period's stored transactions
       (read only).
    2. Run the three **mechanical** checks over the individual records: duplicates
       (same vendor + amount + near date), over-materiality (`abs(amount) >
       materiality_floor`, only if the floor is configured — inert when unset), and
       malformed (missing vendor / date, zero / absent amount).
    3. Return the `AnomalyReport` — flags grouped by kind in a fixed order, each in
       ledger read order. **Advisory: writes nothing canonical, mutates nothing,
       blocks no downstream skill** — resolving any flag is a later, human decision.

    The only ledger-touching argument is a read-side `LedgerSource` — there is no
    writer of any kind, so the skill cannot mutate. **Mechanical only**: every
    check decides from a record's own fields (or the literal equality of a few
    records); none consults history, models a trend, or forecasts — that
    inferential analytics layer is excluded by charter §2.
    """
    transactions = await ledger_source.fetch_for_period(period)

    flags: list[AnomalyFlag] = []
    flags.extend(_duplicate_flags(transactions))
    flags.extend(_over_materiality_flags(transactions, config.materiality_floor))
    flags.extend(_malformed_flags(transactions))

    return AnomalyReport(period=period, flags=tuple(flags))
