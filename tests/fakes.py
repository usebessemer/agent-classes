"""In-memory port fakes for testing the framework core.

These are test doubles only — **no real adapter** (no inbox, model, registry,
or ledger client) lives in the framework. They implement the ports just enough
to exercise the §5 boundary and record what the orchestrator did.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from bookkeeper.config import BookkeeperConfig
from bookkeeper.contracts import Notifier, ReviewQueue, RunLog, RunLogEntry
from bookkeeper.model import ExtractedTransaction, IntakeItem, Transaction
from bookkeeper.ports import (
    AttributionResolver,
    Extractor,
    IntakeSource,
    LedgerSink,
    LedgerSource,
)

# A fixed timestamp so tests are deterministic (no wall-clock reads).
_FIXED_DATE = datetime(2026, 5, 15, 10, 0, 0, tzinfo=timezone.utc)


def make_item(intake_id: str = "item-001", source_hint: str = "Acme Supplies, May 15") -> IntakeItem:
    """Build a generic intake item for tests."""
    return IntakeItem(
        intake_id=intake_id,
        artifact_bytes=b"fake artifact bytes",
        source_hint=source_hint,
        received_at=_FIXED_DATE,
    )


def make_transaction(
    *,
    attribution_target_id: str = "target-001",
    vendor: str = "Acme Supplies",
    amount: Decimal = Decimal("45.99"),
    tax: Decimal = Decimal("3.50"),
    date: datetime | None = None,
    description: str = "",
    artifact_bytes: bytes = b"",
) -> Transaction:
    """Build a stored transaction for read-side / totalling tests.

    `artifact_bytes` defaults to empty, mirroring the `LedgerSource` read-path
    projection (the computation skills total figures; an adapter may omit the
    source blob on the read path). Money is `Decimal` (exact currency, as the
    model now requires); pass `tax=Decimal(...)` (incl. negatives for refunds, or
    `Decimal("0")` for the adapter-coalesced absent-tax case) to drive the totals.
    """
    return Transaction(
        attribution_target_id=attribution_target_id,
        vendor=vendor,
        amount=amount,
        tax=tax,
        date=date or _FIXED_DATE,
        description=description,
        artifact_bytes=artifact_bytes,
    )


def make_config(**overrides) -> BookkeeperConfig:
    """A fully-configured (boundary live) test config; override any §3 field.

    Pass ``confidence_thresholds={}`` to get an inert (unconfigured) boundary.
    """
    base = dict(
        chart_of_accounts=("5000-supplies",),
        accounting_method="cash",
        jurisdiction="XX",
        tax_regime="standard",
        accountant_format="generic-export",
        attribution_targets=("target-001",),
        books_location="generic-ledger",
        intake_channel="generic-channel",
        confidence_thresholds={"attribution": 0.9},
        materiality_floor=1000.0,
    )
    base.update(overrides)
    return BookkeeperConfig.from_mapping(base)


class FakeIntakeSource(IntakeSource):
    """In-memory intake: yields items not yet marked processed."""

    def __init__(self, items: list[IntakeItem] | None = None):
        self.items = list(items) if items is not None else [make_item()]
        self.processed_ids: set[str] = set()

    async def fetch_items(self) -> list[IntakeItem]:
        return [i for i in self.items if i.intake_id not in self.processed_ids]

    async def mark_processed(self, intake_id: str) -> None:
        self.processed_ids.add(intake_id)


class FakeExtractor(Extractor):
    """In-memory extraction: returns a fixed result, or raises a configured error."""

    def __init__(
        self,
        result: ExtractedTransaction | None = None,
        error: Exception | None = None,
    ):
        self.error = error
        self.result = result

    async def extract(self, artifact_bytes: bytes, source_hint: str) -> ExtractedTransaction:
        if self.error is not None:
            raise self.error
        if self.result is not None:
            return self.result
        return ExtractedTransaction(
            vendor="Acme Supplies",
            amount=Decimal("45.99"),
            tax=Decimal("3.50"),
            date=_FIXED_DATE,
            description=f"Artifact: {source_hint}",
        )


class FakeAttributionResolver(AttributionResolver):
    """In-memory resolver: returns a fixed target id (or None for unmatched)."""

    def __init__(self, target_id: str | None = "target-001"):
        self.target_id = target_id

    async def resolve(self, transaction: ExtractedTransaction, source_hint: str) -> str | None:
        return self.target_id


class FakeLedgerSink(LedgerSink):
    """In-memory ledger: records stored transactions, or raises on store."""

    def __init__(self, error: Exception | None = None):
        self.error = error
        self.stored: list[Transaction] = []

    async def store(self, transaction: Transaction) -> None:
        if self.error is not None:
            raise self.error
        self.stored.append(transaction)


class FakeLedgerSource(LedgerSource):
    """In-memory read-side ledger: yields the seeded transactions for a period."""

    def __init__(self, by_period: dict[str, list[Transaction]] | None = None):
        self.by_period = {p: list(txns) for p, txns in (by_period or {}).items()}
        self.fetched: list[str] = []  # periods requested, in order

    async def fetch_for_period(self, period: str) -> list[Transaction]:
        self.fetched.append(period)
        return list(self.by_period.get(period, []))


class FakeLedger(LedgerSource, LedgerSink):
    """Combined read+write fake — one store an adapter implements both ports against.

    Mirrors the real shape (the instance adapter implements `LedgerSink` and
    `LedgerSource` against the same store), so a test can prove a read-side skill
    like `track_tax` writes nothing canonical: `store_calls` stays empty.
    """

    def __init__(self, by_period: dict[str, list[Transaction]] | None = None):
        self.by_period = {p: list(txns) for p, txns in (by_period or {}).items()}
        self.fetched: list[str] = []
        self.store_calls: list[Transaction] = []  # any write the skill must NOT make

    async def fetch_for_period(self, period: str) -> list[Transaction]:
        self.fetched.append(period)
        return list(self.by_period.get(period, []))

    async def store(self, transaction: Transaction) -> None:
        self.store_calls.append(transaction)


class FakeReviewQueue(ReviewQueue):
    """In-memory exceptions pile: records (item, reason, partial) tuples."""

    def __init__(self):
        self.items: list[tuple[IntakeItem, str, ExtractedTransaction | None]] = []

    async def submit(
        self,
        item: IntakeItem,
        reason: str,
        partial: ExtractedTransaction | None = None,
    ) -> None:
        self.items.append((item, reason, partial))


class FakeRunLog(RunLog):
    """In-memory run log: records every entry."""

    def __init__(self):
        self.entries: list[RunLogEntry] = []

    async def record(self, entry: RunLogEntry) -> None:
        self.entries.append(entry)


class FakeNotifier(Notifier):
    """In-memory wake signal: records every notification summary."""

    def __init__(self):
        self.notifications: list[str] = []

    async def notify(self, summary: str) -> None:
        self.notifications.append(summary)
