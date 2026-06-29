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
| `LedgerSink`          | (store)                | `booksLocation` |

The escalation port `ReviewQueue` (charter `flagException`) is part of the
Contract B review substrate and lives in `contracts.py`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from bookkeeper.model import ExtractedTransaction, IntakeItem, Transaction


class IntakeSource(ABC):
    """Pulls transactions/artifacts from the intake channel (§3 `intakeChannel`).

    Capture is idempotent: an item marked processed is never fetched again, so
    the pipeline never double-files.
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
    """Persists an attributed transaction to the canonical ledger (§3 `booksLocation`)."""

    @abstractmethod
    async def store(self, transaction: Transaction) -> None:
        """Persist an attributed transaction."""
