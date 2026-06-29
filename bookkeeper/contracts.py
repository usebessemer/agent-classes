"""The class contracts (charter §6) — interfaces only.

A Standing executor has no PR, so its work product and its review substrate are
defined explicitly as two contracts:

- **Contract A — `PackageWriter`**: the periodic accountant package (the output).
  Interface and docstring only here; the producing skill (`generateAccountantPackage`)
  is a later task.
- **Contract B — the review substrate**: `ReviewQueue` (the exceptions pile),
  `RunLog` (the structured run log), and `Notifier` (the wake signal). These
  replace the PR for an unattended executor and make every run observable.

All three Contract B ports are ABCs; adapters implement them in the private
instance repo. The orchestrator drives them but depends on nothing concrete.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum

from bookkeeper.model import ExtractedTransaction, IntakeItem


# --- Contract A: the accountant package (output) ---------------------------


class PackageWriter(ABC):
    """Contract A — the periodic accountant package (output, ~quarterly).

    Produces the deliverable to the instance's §3 `accountantFormat`: a
    categorized, attribution-costed ledger (every transaction with its
    source-artifact link, account, and target), applicable tax broken out,
    reconciled against the authoritative statements, plus a period summary.
    Per charter §5.4, every computed figure is *proposed* for sign-off, never
    auto-published.

    Interface only. The producing skill (`generateAccountantPackage`) is a
    later task; this fixes the seam so adapters and that skill agree on shape.
    """

    @abstractmethod
    async def generate_package(self, period: str) -> None:
        """Assemble and write the accountant package for `period` (e.g. "2026-Q2")."""


# --- Contract B: the review substrate (queue + run log + notify) -----------


class ReviewQueue(ABC):
    """Contract B — the exceptions pile (charter `flagException`).

    The single escalation primitive under the whole §5 boundary: anything
    uncertain, consequential, or failed is submitted here with the reason it
    escalated and the agent's best partial proposal. The human reads the pile
    and returns the decision.
    """

    @abstractmethod
    async def submit(
        self,
        item: IntakeItem,
        reason: str,
        partial: ExtractedTransaction | None = None,
    ) -> None:
        """Submit an item for human review.

        Args:
            item: The intake item (source artifact + metadata for context).
            reason: Why it escalated — the §5 reason (unmatched, failed, inert).
            partial: Any partial extraction, when one was produced before escalating.
        """


class RunOutcome(str, Enum):
    """How the pipeline disposed of a single intake item in a run."""

    AUTO_FILED = "auto_filed"
    ROUTED_TO_REVIEW = "routed_to_review"


@dataclass(frozen=True)
class RunLogEntry:
    """One structured run-log record (charter §6: processed / auto-filed / routed-and-why).

    Formalizes what instance #1 emitted ad hoc via `logging`, so a run is
    reproducible from the trail (charter §1: every period reproducible).
    """

    intake_id: str
    outcome: RunOutcome
    reason: str = ""
    attribution_target_id: str | None = None


class RunLog(ABC):
    """Contract B — the structured run log.

    Records the disposition of every item the orchestrator processes: what was
    auto-filed (to which target) and what was routed to review and why.
    """

    @abstractmethod
    async def record(self, entry: RunLogEntry) -> None:
        """Append one run-log entry."""


class Notifier(ABC):
    """Contract B — the wake notification.

    The wake signal a Standing executor raises after a run so the human-side
    reviewer knows the queue has items to attend to. A signal, not a courier.
    """

    @abstractmethod
    async def notify(self, summary: str) -> None:
        """Send a run summary (counts + that review items await)."""
