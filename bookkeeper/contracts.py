"""The class contracts (charter §6) — interfaces only.

A Standing executor has no PR, so its work product and its review substrate are
defined explicitly as two contracts:

- **Contract A — `PackageWriter`**: the **write-side** of the periodic accountant
  package. The general package is *assembled* by the `generateAccountantPackage`
  skill (proposed, returned, format-agnostic); this port is the instance's gated
  publish step that renders that assembled package to the instance's
  `accountantFormat` and writes it out — invoked separately, on human approval,
  never by the skill (§5.4).
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
from typing import TYPE_CHECKING

from bookkeeper.model import ExtractedTransaction, IntakeItem

if TYPE_CHECKING:
    # Type-only: the assembled package is produced by the `generateAccountantPackage`
    # skill. Imported under TYPE_CHECKING (with `from __future__ import annotations`,
    # the annotation is a string at runtime) so the framework core keeps no runtime
    # dependency on the skills layer and no import cycle is created.
    from bookkeeper.skills.generate_package import AccountantPackage


# --- Contract A: the accountant package (output) ---------------------------


class PackageWriter(ABC):
    """Contract A — the **write-side** of the periodic accountant package (~quarterly).

    The gated publish step, **separated from assembly** per §5.4. The general
    Contract A package — a categorized, attribution-costed ledger (every
    transaction with its source-artifact link, account, and target), applicable
    tax broken out, the reconciliation result, plus a period summary — is
    *assembled* by the `generateAccountantPackage` skill: proposed, returned,
    `accountantFormat`-agnostic, writing nothing. **This port** takes that
    assembled package and renders it to the instance's §3 `accountantFormat` (a QBO
    export, a spreadsheet, …), writing the deliverable out.

    It is the **instance's gated, human-approved publish step** — an adapter in the
    private instance repo, invoked separately **on human approval, never by the
    skill** (the skill cannot publish; it has no writer). Per charter §5.4 every
    computed figure is *proposed* for sign-off until this step runs, never
    auto-published.

    Interface only — the framework holds the seam; the concrete renderer (the
    format adapter) lives in the private instance repo.
    """

    @abstractmethod
    async def write_package(self, package: AccountantPackage) -> None:
        """Render the assembled `package` to the instance's `accountantFormat` and write it.

        The gated publish step (§5.4): called separately on human approval with the
        package the `generateAccountantPackage` skill assembled — never by that
        skill. `package.period` identifies the period (e.g. "2026-Q2")."""


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
