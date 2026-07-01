"""Skill ports — the abstract interfaces the orchestrator drives.

Each port is one of the charter §4 BUILT skills, generalized to the §3
vocabulary. The orchestrator depends only on these ABCs; concrete adapters
(a specific inbox, model, registry, or ledger) implement them and live in the
private instance repo — never in this framework.

| port                  | charter skill          | §3 field        |
|-----------------------|------------------------|-----------------|
| `IntakeSource`        | `intakeTransaction`    | `intakeChannel` |
| `Extractor`           | `extractFields`        | —               |
| `AttributionResolver` | `attributeTransaction` | `attributionTargets` |
| `LedgerSink`          | (store, write)         | `booksLocation` |
| `LedgerSource`        | (read)                 | `booksLocation` |
| `StatementSource`     | `reconcileAccount` (read) | `booksLocation` |

The escalation port `ReviewQueue` (charter `flagException`) is part of the
Contract B review substrate and lives in `contracts.py`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from bookkeeper.model import (
    ExtractedTransaction,
    IntakeItem,
    StatementLine,
    Transaction,
)


class IntakeSource(ABC):
    """Pulls transactions/artifacts from the intake channel (§3 `intakeChannel`).

    Capture is idempotent: an item **successfully** marked processed is never
    fetched again. This is the *first* line of the no-double-file guarantee, not
    the only one. On the auto-file path the orchestrator stores a transaction and
    *then* marks the item processed as a separate step; if `mark_processed` fails
    after the store succeeded, the item is not marked and **will** be re-fetched
    next run. The guarantee then rests on the *second* line — an idempotent
    `LedgerSink.store`, whose re-store is a no-op. So: mark-processed prevents the
    re-fetch; idempotent store prevents a duplicate if the re-fetch happens anyway.
    """

    @abstractmethod
    async def fetch_items(self) -> list[IntakeItem]:
        """Return all intake items awaiting processing."""

    @abstractmethod
    async def mark_processed(self, intake_id: str) -> None:
        """Mark an item processed so it is not fetched again."""


class Extractor(ABC):
    """Reads a source artifact into structured fields (charter `extractFields`)."""

    @abstractmethod
    async def extract(
        self, artifact_bytes: bytes, source_hint: str
    ) -> ExtractedTransaction:
        """Extract structured transaction fields from a source artifact.

        Money fields (`amount`, `tax`) are `Decimal` — exact currency, never
        `float`. Absent / NULL tax is the adapter's to coalesce: construct the
        `ExtractedTransaction` with `tax=Decimal("0")`, never `None`. The
        framework holds no None-money, so no downstream skill has to re-launder
        a float or guard a missing value.

        Args:
            artifact_bytes: The raw source artifact (image, PDF, CSV row, ...).
            source_hint: Channel-provided hint (subject/memo/filename), or "".
        """


class AttributionResolver(ABC):
    """Matches a transaction to its attribution target (§3 `attributionTargets`).

    Returns a target id only when matched with sufficient confidence against an
    existing entity. The framework never creates a new target and never guesses:
    an uncertain match returns `None`, which the orchestrator routes to review.
    """

    @abstractmethod
    async def resolve(
        self, transaction: ExtractedTransaction, source_hint: str
    ) -> str | None:
        """Return the matched `attribution_target_id`, or `None` to route to review."""


class LedgerSink(ABC):
    """Persists an attributed transaction to the canonical ledger (§3 `booksLocation`).

    `store` MUST be **idempotent on a stable key**: re-storing a transaction that
    is already filed is a no-op, never a duplicate row. This is the *second* line
    of the no-double-file guarantee (`IntakeSource` is the first). The orchestrator
    files, then marks the item processed as a separate step; if the mark fails
    after a successful store, the item is re-fetched and re-stored next run — an
    idempotent store makes that re-store harmless.

    The "stable key" is the adapter's choice, derived deterministically from what
    it persists so the *same* source artifact always maps to the *same* key: a
    real adapter dedupes on the source-item linkage it stores (the intake id the
    transaction came from) or, absent that, on the transaction's natural business
    key. A reference/fake store honors the same contract.
    """

    @abstractmethod
    async def store(self, transaction: Transaction) -> None:
        """Persist an attributed transaction.

        Idempotent on a stable key: a re-store of an already-filed transaction is
        a no-op, never a duplicate (see the class docstring for why the pipeline
        relies on this).
        """


class LedgerSource(ABC):
    """Reads stored transactions back from the ledger (read side of §3 `booksLocation`).

    The read complement to `LedgerSink`. The write path (`StandingRun`) files
    transactions; the computation skills (`trackTax` and, later, reconcile /
    closePeriod) read a period back to total and compare. An instance adapter
    implements both `LedgerSink` (write) and `LedgerSource` (read) against the
    same store, so the framework reads exactly what it filed.

    Read-path projection: the computation skills total figures, they do not need
    the source bytes. An adapter MAY omit heavy fields on this path (return
    `Transaction.artifact_bytes = b""`) rather than load every blob to sum
    numbers; the figure stays traceable through the ledger row itself. Aggregation
    is the framework's job, not the adapter's: an adapter returns the period's
    transactions, the regime rules and totalling live in the skill.

    Money is `Decimal`, never `float` (exact currency). A ledger that stores tax
    nullable must coalesce NULL → `Decimal("0")` when reconstructing the model
    (the SQL `COALESCE(tax, 0)` belongs on this side of the line), so the
    framework totals exact Decimals and never None-money.
    """

    @abstractmethod
    async def fetch_for_period(self, period: str) -> list[Transaction]:
        """Return all stored transactions for `period` (e.g. "2026-Q2")."""


class StatementSource(ABC):
    """Reads the authoritative bank / card statement for a period (read-only).

    The reconcile counterpart to `LedgerSource`: `LedgerSource` reads what the
    books *captured*, `StatementSource` reads what the bank / card issuer *says
    happened*, and `reconcile_account` matches the two and surfaces every gap.

    Read-only by design. Reconcile is **detection-only** — it mutates nothing
    (§5.5) — so there is deliberately no statement *writer* port: the framework
    never edits the authoritative feed, and resolution of any gap is always a
    later, human-gated step. An adapter implements only this read method.

    The concrete adapter reads a real statement feed and lives in the private
    instance repo. Honest scope note: an instance that ingests only individual
    source artifacts (and has no statement feed yet) has nothing to implement
    this against — so reconcile's *value* there waits on a feed — but the port,
    skill, and matcher are fully built and testable now against an in-memory fake.
    """

    @abstractmethod
    async def fetch_statement(self, period: str) -> list[StatementLine]:
        """Return all authoritative statement lines for `period` (e.g. "2026-Q2")."""
