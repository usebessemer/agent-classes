"""`trackTax` — break out + total reclaimable tax per target and period.

The first computation skill on the §5 core. It reads a period of stored
transactions, classifies each transaction's applicable tax under the
config-selected **tax regime**, totals the reclaimable tax **per attribution
target** and **for the period**, and returns a *proposed* `TaxSummary`.

The §5 boundary, preserved exactly:

- **Computation is autonomous** (charter §5 class default): trackTax reads,
  classifies, and totals; it **mutates nothing**. Its only argument that touches
  the ledger is a read-side `LedgerSource` — there is no sink, no package writer,
  no system-of-record handle in this module, so it *cannot* publish.
- **§5.4 — proposed, never auto-published.** `track_tax` *returns* the summary;
  filing it into Contract A (the accountant package) or the system of record is a
  later, human-gated step (`generateAccountantPackage`). A test pins that this
  function writes nothing canonical.
- **§5.3 — uncertain tax treatment → review, not silent totalling.** A regime may
  classify a transaction as ambiguous (e.g. tax captured but no target it can be
  attributed to); the skill surfaces those in `TaxSummary.flagged` and **excludes
  them from the totals**, rather than quietly folding them in. The gated step
  routes the flagged list to the `ReviewQueue`.

**Regime seam (config-selected by `config.tax_regime`).** A `TaxRegime` answers
"how much of this transaction's tax is reclaimable?" for one jurisdiction. v1
registers **only `HST`** (Canada): HST is a fully reclaimable input tax credit,
so reclaimable = the full captured tax, absent tax → 0. An unregistered
`tax_regime` **fails fast** (`UnknownTaxRegime`) rather than silently totalling
nothing. The seam is built so VAT / US sales tax / etc. can register later
without touching the skill; v1 builds and validates HST only.

**Decimal money.** All tax arithmetic is `Decimal`, never `float`. The model's
`amount`/`tax` are `float` today (a latent precision issue flagged for a
follow-up — not fixed here); this skill converts each value to `Decimal` *at the
boundary* via `Decimal(str(x))` (string conversion, so 0.10 + 0.20 totals to an
exact 0.30, not 0.30000000000000004) and totals only in `Decimal`. This matches
instance #1's HST report, which also totals in `Decimal`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal

from bookkeeper.config import BookkeeperConfig
from bookkeeper.model import Transaction
from bookkeeper.ports import LedgerSource

# Decimal zero, reused so totals start and stay Decimal (never coerced to float).
_ZERO = Decimal("0")


def _to_decimal(value: float | int | None) -> Decimal:
    """Convert a model money field to `Decimal` at the boundary.

    Goes through `str(...)` so a stored float renders to its decimal literal
    (e.g. ``3.50`` → ``Decimal("3.5")``), not its binary-float expansion. A
    `None` (NULL / absent tax) counts as 0 — pre-pipeline history that never
    captured tax contributes nothing, exactly as the reference's `COALESCE` does.
    """
    if value is None:
        return _ZERO
    return Decimal(str(value))


# --- The tax-regime seam (config-selected) ---------------------------------


@dataclass(frozen=True)
class TaxLine:
    """One transaction's tax classification under a regime.

    `reclaimable` is the Decimal tax the regime says can be reclaimed for this
    transaction (0 when none or when ambiguous). `ambiguous=True` means the
    regime cannot confidently total this transaction's tax (§5.3) — the skill
    flags it for review instead of folding it into a target total, and `reason`
    carries the human-readable why.
    """

    transaction: Transaction
    reclaimable: Decimal
    ambiguous: bool = False
    reason: str = ""


class TaxRegime(ABC):
    """A jurisdiction's tax rule: how much of a transaction's tax is reclaimable.

    The config-selected seam (`config.tax_regime`) that keeps the regime rules in
    the framework and out of adapter SQL. Constructed with the instance config so
    a regime can consult jurisdiction / owner policies; v1's HST regime needs
    none of that. Register a regime in `_REGIMES`; the skill never hard-codes one.
    """

    #: The registry key + the name recorded on the produced summary.
    name: str = ""

    def __init__(self, config: BookkeeperConfig):
        self.config = config

    @abstractmethod
    def classify(self, transaction: Transaction) -> TaxLine:
        """Classify one transaction's tax → reclaimable amount (and ambiguity)."""


class HstRegime(TaxRegime):
    """Canada / HST: HST is a fully reclaimable input tax credit.

    Reclaimable = the full captured `tax` (the business claims it back, so it is a
    pass-through, not a cost — instance #1's "reclaimable pass-through" decision).
    Absent / NULL tax → 0. Negative tax (a refund or credit, signed negative)
    reduces the reclaimable total, carrying the same sign as the refunded spend.

    Ambiguity (§5.3) for HST is uniform-rule-low: the one genuinely ambiguous case
    is **tax captured on a transaction with no attribution target it can be tied
    to** — there is reclaimable tax but nowhere to attribute it, so it is flagged
    for review rather than dropped or mis-totalled. Everything else is "sum what
    was captured", matching the reference report.
    """

    name = "HST"

    def classify(self, transaction: Transaction) -> TaxLine:
        reclaimable = _to_decimal(transaction.tax)
        if reclaimable != _ZERO and not (transaction.attribution_target_id or "").strip():
            return TaxLine(
                transaction,
                _ZERO,
                ambiguous=True,
                reason="Captured tax with no resolvable attribution target — "
                "routed to review rather than totalled (§5.3).",
            )
        return TaxLine(transaction, reclaimable)


# The v1 regime registry. **HST only** — the seam supports more, but no
# speculative breadth is built or registered until a jurisdiction needs it.
_REGIMES: dict[str, type[TaxRegime]] = {
    HstRegime.name: HstRegime,
}


class UnknownTaxRegime(ValueError):
    """Raised when `config.tax_regime` names a regime not registered in v1.

    The §5 fail-safe for the regime seam: an unknown regime fails fast with a
    clear message rather than silently totalling nothing (which would understate
    every reclaim).
    """


def select_regime(config: BookkeeperConfig) -> TaxRegime:
    """Select the tax regime for `config.tax_regime`, or fail fast.

    Matching is case-insensitive (``"hst"`` → ``HST``). An unregistered regime
    raises `UnknownTaxRegime`; v1 registers HST (Canada) only.
    """
    key = (config.tax_regime or "").strip().upper()
    regime_cls = _REGIMES.get(key)
    if regime_cls is None:
        raise UnknownTaxRegime(
            f"Unknown tax_regime {config.tax_regime!r}; "
            f"registered regimes: {sorted(_REGIMES)}. v1 supports HST (Canada) "
            "only — an unregistered regime fails fast rather than totalling nothing."
        )
    return regime_cls(config)


# --- The result model (proposed, traceable) --------------------------------


@dataclass(frozen=True)
class TargetTax:
    """Reclaimable tax totalled for one attribution target, traceable to its lines.

    `reclaimable` is the Decimal sum over `transactions` — every figure links
    back to the exact transactions it was built from (charter §1: fully
    traceable), each of which links to its source artifact via the ledger.
    """

    attribution_target_id: str
    reclaimable: Decimal
    transactions: tuple[Transaction, ...]

    @property
    def transaction_count(self) -> int:
        return len(self.transactions)


@dataclass(frozen=True)
class TaxFlag:
    """A transaction the regime could not confidently total (§5.3).

    Surfaced for review and **excluded from the totals** — proposed for a human,
    not silently folded in. The gated step routes these to the `ReviewQueue`.
    """

    transaction: Transaction
    reason: str


@dataclass(frozen=True)
class TaxSummary:
    """A **proposed** reclaimable-tax summary for a period (charter `trackTax`).

    Proposed, never published (§5.4): `track_tax` returns this; it writes nothing
    to the ledger, the system of record, or the accountant package. Carries the
    per-target totals, the period total (sum of the per-target totals, in
    `Decimal`), and the §5.3 flagged exceptions kept out of those totals. Records
    which `regime` produced it and for which `period`, so the figures stay
    reproducible from the trail.
    """

    period: str
    regime: str
    per_target: tuple[TargetTax, ...]
    period_total: Decimal
    flagged: tuple[TaxFlag, ...] = field(default_factory=tuple)


# --- The skill operation ----------------------------------------------------


async def track_tax(
    ledger_source: LedgerSource,
    config: BookkeeperConfig,
    period: str,
) -> TaxSummary:
    """Total reclaimable tax per target and for `period` — proposed, not published.

    1. Select the tax regime from `config.tax_regime` (HST only in v1; an unknown
       regime fails fast before any read).
    2. `fetch_for_period(period)` → the period's stored transactions (read only).
    3. Classify each via the regime → reclaimable Decimal (or flagged ambiguous).
    4. Aggregate per `attribution_target_id`, plus the period total.
    5. Return the `TaxSummary` — proposed for sign-off, **never auto-published**.

    Writes nothing canonical: the only ledger-touching argument is a read-side
    `LedgerSource`. Aggregation happens here, in the framework, not in adapter SQL.
    """
    # Fail fast on an unknown regime *before* reading anything — a misconfigured
    # instance gets a clear error, never a silently-empty total.
    regime = select_regime(config)

    transactions = await ledger_source.fetch_for_period(period)

    by_target: dict[str, list[tuple[Transaction, Decimal]]] = {}
    flagged: list[TaxFlag] = []
    for transaction in transactions:
        line = regime.classify(transaction)
        if line.ambiguous:
            # §5.3: surface for review, do not fold into any target total.
            flagged.append(TaxFlag(line.transaction, line.reason))
            continue
        target_id = (transaction.attribution_target_id or "").strip()
        if not target_id:
            # No tax to reclaim and no target to attribute to — nothing to total.
            # (Tax-without-target was already flagged above as ambiguous.)
            continue
        by_target.setdefault(target_id, []).append((transaction, line.reclaimable))

    per_target: list[TargetTax] = []
    period_total = _ZERO
    for target_id in sorted(by_target):  # deterministic, stable ordering
        lines = by_target[target_id]
        target_total = sum((reclaimable for _txn, reclaimable in lines), _ZERO)
        per_target.append(
            TargetTax(
                attribution_target_id=target_id,
                reclaimable=target_total,
                transactions=tuple(txn for txn, _reclaimable in lines),
            )
        )
        period_total += target_total

    return TaxSummary(
        period=period,
        regime=regime.name,
        per_target=tuple(per_target),
        period_total=period_total,
        flagged=tuple(flagged),
    )
