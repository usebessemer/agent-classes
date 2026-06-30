"""`generateAccountantPackage` — assemble the Contract A deliverable (proposed, never published).

The terminal §5.4 skill: the composition step that turns a **closed period** into
**Contract A** (charter §6), the deliverable the whole class exists to produce. It
sits over the full merged pipeline (capture → attribute → categorize → trackTax →
reconcile → closePeriod) and takes the period's `CloseReport` — the already-
assembled close — and either composes the proposed package (the close is `READY`)
or refuses (the close is `BLOCKED`). It composes; it does not re-run the pipeline,
re-validate the close, or strike any figure anew — every number comes verbatim
from the reports the close already bundled.

The §5 boundary, preserved exactly (§5.4 — *proposed, never auto-published*):

- **Assemble + propose, never publish.** `generate_accountant_package` *returns*
  an `AccountantPackage`; it writes nothing to the ledger, the system of record,
  the review queue, or a file, and it **never calls a writer / pushes to a system
  of record**. There is no sink, writer, queue, or `PackageWriter` among its
  arguments, so it *cannot* publish — the package's status is `PROPOSED`, never a
  published/filed state. Rendering the package to the instance's
  `accountant_format` and writing it out is the gated, human-approved publish step
  — the **instance's** `PackageWriter` adapter (Contract A), invoked separately,
  never by this skill. A mutation-proven test pins writes-nothing.
- **Refuse to package an unclosed period.** Only a `READY` close yields a
  deliverable. Given a `BLOCKED` close (open items remain), the skill produces
  **no package over an unclosed period** — it returns a `BLOCKED`
  `AccountantPackage` naming the unmet close (which preconditions still block),
  with no entries, breakout, or reconciliation attached. A test pins this.
- **Never file / transmit** (charter NEVER list). The package is *prepared*; the
  accountant / owner files it. This skill produces the package; it never transmits
  to a tax authority or anywhere external — there is no external sink here at all.

**`accountant_format`-agnostic (the assemble-vs-write split).** The skill
assembles the **general** Contract A content — the categorized, attribution-costed
ledger entries, the tax broken out per target + period, the reconciliation result,
and the period summary — with no QBO / spreadsheet / format specifics. Rendering
that general package to the instance's concrete `accountant_format` is the
adapter's job (`PackageWriter.write_package`), kept off this dependency-free core.

**Pure, deterministic, port-free.** Like `close_period`, this is a pure function
of its inputs — it reads no source, takes no port, and so is sync (no `await`),
trivial to test, and structurally incapable of mutating anything. Ordering is
deterministic: entries follow the categorization report's order (which preserves
the ledger read order); the tax breakout and reconciliation are attached verbatim,
each already deterministically ordered by the skill that produced it.

**v1 note (mirrors `close_period`).** The human sign-off / interaction surface
isn't built, so v1 treats a `READY` close as the closeable state and the package
as `PROPOSED` (§5.4 keeps it un-published regardless of when sign-off lands). The
signing surface and the concrete `accountant_format` adapter are future / instance
pieces — not built here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum

from bookkeeper.config import BookkeeperConfig
from bookkeeper.model import Transaction
from bookkeeper.skills.categorize import CategoryProposal
from bookkeeper.skills.close_period import CloseReport, CloseStatus
from bookkeeper.skills.reconcile import ReconciliationReport
from bookkeeper.skills.track_tax import TaxSummary


# --- The result model (proposed, traceable) --------------------------------


class PackageStatus(str, Enum):
    """Whether the Contract A package was assembled, or refused over an open close.

    A `str` enum (like `CloseStatus` / `RunOutcome`) so the status serializes to a
    stable, readable tag. There is **deliberately no** ``PUBLISHED`` / ``FILED``
    member: `generate_accountant_package` never publishes — the most it produces is
    a `PROPOSED` package awaiting human sign-off and the instance's gated
    `PackageWriter` (§5.4).
    """

    #: The close was `READY` — the Contract A package is assembled and proposed.
    PROPOSED = "proposed"
    #: The close was not `READY` — no deliverable; the unmet close is named.
    BLOCKED = "blocked"


@dataclass(frozen=True)
class PackageEntry:
    """One costed / categorized / taxed ledger line in the package (proposed).

    Assembles, for a single transaction, the three things the deliverable joins:
    the **proposed account** (the categorization), the **attribution target** and
    the **source-artifact link** (both carried on the transaction), and the **tax**
    captured on the line. It holds the `categorization` proposal verbatim
    (assembly, not re-formatting — like `AssembledPeriod` holds the reports) and
    exposes the joined figures as first-class accessors, so a `PackageWriter`
    adapter can render a row without reaching into the proposal's internals. Every
    figure traces back: the account to the categorization report (with its
    confidence + which rule fired), the amount / target / tax / source link to the
    transaction's own source artifact (charter §1: fully traceable).
    """

    categorization: CategoryProposal

    @property
    def transaction(self) -> Transaction:
        """The underlying transaction — its `artifact_bytes` is the source link."""
        return self.categorization.transaction

    @property
    def account(self) -> str:
        """The proposed chart account (always in `config.chart_of_accounts`, §5.2)."""
        return self.categorization.proposed_account

    @property
    def attribution_target_id(self) -> str:
        """The §3 attribution target the transaction was costed to."""
        return self.categorization.transaction.attribution_target_id

    @property
    def tax(self) -> Decimal:
        """The tax captured on this line (exact `Decimal`).

        The per-line figure that rolls up into the `tax_breakout`; for the v1 HST
        regime it is the line's reclaimable tax. The regime-aware per-target and
        period reclaimable totals live in `AccountantPackage.tax_breakout` — this
        skill attaches the captured figure rather than re-running the regime.
        """
        return self.categorization.transaction.tax


@dataclass(frozen=True)
class PackageSummary:
    """The period's disposition counts for the package (Contract A's summary).

    The same counts `close_period` struck for the period, carried onto the
    deliverable so Contract A is self-describing (a `PackageWriter` renders the
    package without reaching back into the close): `processed` is every transaction
    the period covered; `auto_filed` the ones handled confidently; `reviewed` the
    ones surfaced for a human; `open` the outstanding items. A `PROPOSED` package
    is built from a `READY` close, so ``open == 0`` and
    ``processed == auto_filed + reviewed`` by construction.
    """

    processed: int
    auto_filed: int
    reviewed: int
    open: int


@dataclass(frozen=True)
class AccountantPackage:
    """The Contract A deliverable — **proposed, never published** (§5.4).

    What `generate_accountant_package` returns. Proposed, never published (§5.4):
    the skill returns this; it writes nothing to the ledger, the system of record,
    the queue, or a file, and never calls a `PackageWriter`. When `status` is
    `PROPOSED` (the close was `READY`), it carries the full Contract A content —
    the `summary`, the costed / categorized / taxed `entries`, the `tax_breakout`
    (per target + period), and the `reconciliation` result — and `unmet_close` is
    `None`. When `status` is `BLOCKED` (the close was not `READY`), there is **no
    deliverable**: `summary` / `tax_breakout` / `reconciliation` are `None`,
    `entries` is empty, and `unmet_close` names what still blocks the close. The
    package is `accountant_format`-agnostic: rendering it to the instance's concrete
    format is the gated `PackageWriter` step. Ordering is deterministic.
    """

    period: str
    status: PackageStatus
    #: The accounting basis the books were kept on (§3 `accounting_method`),
    #: recorded so the deliverable states its basis — generic, not format-specific.
    accounting_method: str
    #: The §3 `jurisdiction` the period was kept under (generic context).
    jurisdiction: str
    summary: PackageSummary | None = None
    entries: tuple[PackageEntry, ...] = field(default_factory=tuple)
    tax_breakout: TaxSummary | None = None
    reconciliation: ReconciliationReport | None = None
    #: Set only when `BLOCKED`: the §1-traceable why no package was produced.
    unmet_close: str | None = None


# --- Assembly helpers (pure, deterministic) ---------------------------------


def _unmet_close_reason(close_report: CloseReport) -> str:
    """The §1-traceable why a non-`READY` close yields no package (§5.4 refusal).

    Names the close being refused and which preconditions still block it, so the
    blocked package points a human straight at what must clear before the period
    can be packaged. Reads the close's own blockers; produces no deliverable over
    an unclosed period.
    """
    failed_checks = sorted({blocker.check for blocker in close_report.blockers})
    blocked_on = ", ".join(failed_checks) if failed_checks else "the close is not READY"
    return (
        f"Close for period {close_report.period!r} is {close_report.status.value.upper()} "
        f"with {len(close_report.blockers)} open item(s) — blocked on: {blocked_on}. "
        f"No accountant package is produced over an unclosed period; the close must "
        f"reach READY first (§5.4: proposed, never published over an open close)."
    )


# --- The skill operation ----------------------------------------------------


def generate_accountant_package(
    close_report: CloseReport,
    config: BookkeeperConfig,
) -> AccountantPackage:
    """Assemble `close_report`'s period into Contract A — proposed, never published (§5.4).

    1. Refuse a non-`READY` close: if the close is `BLOCKED` (or carries no
       proposed close), produce **no deliverable** — return a `BLOCKED`
       `AccountantPackage` naming the unmet close. Never package an unclosed period.
    2. Otherwise compose the `READY` close's bundled reports into Contract A:
       - **entries** — one costed / categorized / taxed ledger line per
         categorized transaction (the categorization report's proposals, in order),
         each carrying its proposed account, attribution target, source link, and
         captured tax;
       - **tax_breakout** — the `TaxSummary` (per-target + period totals) verbatim;
       - **reconciliation** — the `ReconciliationReport` verbatim;
       - **summary** — the period's disposition counts.
    3. Return the `AccountantPackage` — `PROPOSED`, **never published**.

    A pure function of the close + config: it reads no source, takes no sink /
    writer / queue / `PackageWriter`, and **writes nothing canonical** — it cannot
    mutate the ledger, the system of record, or the queue, and never renders or
    files the package. Rendering to the instance's `accountant_format` and writing
    it out is the gated, human-approved `PackageWriter` step (Contract A), invoked
    separately, never here. The package is `accountant_format`-agnostic; `config`
    supplies only the generic §3 basis (`accounting_method`, `jurisdiction`) stamped
    on the deliverable.
    """
    # §5.4: refuse to package an unclosed period. Only a READY close (which carries
    # an assembled proposed_close) yields a deliverable; anything else → BLOCKED,
    # no package, naming the unmet close. Fail-safe: a degenerate READY with no
    # proposed_close is also refused rather than packaged empty.
    if close_report.status is not CloseStatus.READY or close_report.proposed_close is None:
        return AccountantPackage(
            period=close_report.period,
            status=PackageStatus.BLOCKED,
            accounting_method=config.accounting_method,
            jurisdiction=config.jurisdiction,
            unmet_close=_unmet_close_reason(close_report),
        )

    # READY: compose the close's already-assembled reports into Contract A. Every
    # figure comes verbatim from the reports the close bundled — assembly, not a
    # re-run. The close already validated period coherence and cleanliness, so no
    # figure is re-struck or re-validated here.
    assembled = close_report.proposed_close.assembled
    close_summary = close_report.proposed_close.summary

    # Entries: one ledger line per categorized transaction, in the categorization
    # report's order (which preserves the ledger read order) — deterministic. A
    # READY close has nothing flagged, so the proposals cover every transaction.
    entries = tuple(
        PackageEntry(categorization=proposal)
        for proposal in assembled.categorization.proposals
    )

    summary = PackageSummary(
        processed=close_summary.processed,
        auto_filed=close_summary.auto_filed,
        reviewed=close_summary.reviewed,
        open=close_summary.open,
    )

    return AccountantPackage(
        period=close_report.period,
        status=PackageStatus.PROPOSED,
        accounting_method=config.accounting_method,
        jurisdiction=config.jurisdiction,
        summary=summary,
        entries=entries,
        tax_breakout=assembled.tax_summary,
        reconciliation=assembled.reconciliation,
        unmet_close=None,
    )
