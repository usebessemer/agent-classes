"""The framework data model — frozen dataclasses, vertical-agnostic.

These are the units the §5 pipeline moves through: a captured artifact
(`IntakeItem`), the structured fields read from it (`ExtractedTransaction`),
and a transaction attributed and ready for the ledger (`Transaction`). They
carry no client- or channel-specific shape: a row works equally for a scanned
image, a PDF invoice, a CSV line, or a bank-feed record.

Generalized from instance #1's data model (see the issue's generalization
map). The notable moves:

- `image_bytes` → `artifact_bytes` — not every source artifact is an image.
- `subject` → `source_hint` — a generic, channel-provided attribution hint
  (an email subject line, a memo field, a filename), not an email concept.
- `merchant` → `vendor`; `Expense.job_id` → `Transaction.attribution_target_id`
  — the generic attribution dimension from charter §3 `attributionTargets`.
- the `kind`/`ExpenseKind` enum is dropped entirely: category is a planned skill
  driven by the per-instance `chartOfAccounts`, not an extraction output.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True)
class IntakeItem:
    """A captured source artifact awaiting extraction.

    The generic unit an `IntakeSource` yields: a stable id (for idempotent
    mark-processed), the raw source artifact bytes, and an optional
    channel-provided attribution hint. `source_hint` is free text the
    `AttributionResolver` may use to match a target; it is never required and
    never channel-specific.
    """

    intake_id: str
    artifact_bytes: bytes
    source_hint: str = ""
    received_at: datetime | None = None


@dataclass(frozen=True)
class ExtractedTransaction:
    """Structured fields read from a source artifact (charter `extractFields`).

    No attribution and no category: extraction reports what the artifact says,
    not where it belongs. Attribution is the resolver's job; categorization is a
    later, review-gated skill.

    Money is `Decimal`, never `float` (currency is exact, float is lossy). An
    `Extractor` adapter constructs these from its source; absent / NULL tax is
    the adapter's to coalesce to `Decimal("0")` at the boundary, so the framework
    never holds None-money (see `Extractor.extract`).
    """

    vendor: str
    amount: Decimal
    tax: Decimal
    date: datetime
    description: str


@dataclass(frozen=True)
class Transaction:
    """An extracted transaction attributed to a target and ready for the ledger.

    `attribution_target_id` is the resolved §3 `attributionTargets` id — the
    single piece of state that distinguishes an auto-fileable transaction from
    one that must go to review. Carries the source `artifact_bytes` so every
    stored figure stays linked to its source (charter §1: fully traceable).

    Money is `Decimal` (exact currency). On the read path, a `LedgerSource`
    adapter reconstructs these from the ledger and coalesces absent / NULL tax to
    `Decimal("0")` before constructing the model (see `LedgerSource`).
    """

    attribution_target_id: str
    vendor: str
    amount: Decimal
    tax: Decimal
    date: datetime
    description: str
    artifact_bytes: bytes


@dataclass(frozen=True)
class StatementLine:
    """One line of the authoritative bank / card statement (read-only).

    The counterpart to a ledger `Transaction` on the reconcile path: what the
    bank or card issuer says happened, against which the captured books are
    matched (`reconcileAccount`). Carries a stable `statement_ref` so every
    matched pair and every surfaced gap links back to the exact statement line it
    came from (charter §1: fully traceable).

    Money is `Decimal` (exact currency, never `float`): reconcile matches amounts
    by exact Decimal equality, so a difference is a real discrepancy, not a
    rounding artifact. `description` is the free-text vendor / description the
    statement shows (a single string — a statement renders one line per charge),
    used as the fuzzy disambiguator when several candidates share an amount and a
    date.

    Like the rest of this model, the row is source-agnostic: a bank export, a
    card feed, or a processor settlement file all reduce to the same shape. The
    concrete `StatementSource` adapter constructs these from a real feed and
    lives in the private instance repo; the framework holds only this shape.
    """

    statement_ref: str
    date: datetime
    amount: Decimal
    description: str
