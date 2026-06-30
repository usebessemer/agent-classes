"""The Bookkeeper skills — the charter §4 computation skills, one module each.

A skill is a single framework operation built on the agnostic core (ports +
model + config). Skills compute over what the pipeline stored; they keep the
regime / jurisdiction rules in the framework, off the adapter. Each is
vertical-agnostic and adapter-free: it drives ports, never a concrete system.

Built here:

- `track_tax` — break out + total reclaimable tax per attribution target and
  period (charter `trackTax`). Returns a *proposed* `TaxSummary`; per §5.4 it
  writes nothing canonical. Tax regime selected by `config.tax_regime`.
- `categorize` — propose a chart account per transaction (charter
  `categorizeTransaction`). Returns a *proposed* `CategorizationReport`; per
  §5.4 it writes nothing canonical. Categories come from
  `config.chart_of_accounts`; never invents one (§5.2).
- `reconcile_account` — match the captured ledger against the authoritative
  statement and surface every gap (charter `reconcileAccount`). Returns a
  detection-only `ReconciliationReport`; per §5.5 it writes nothing canonical and
  never resolves a mismatch. Reads via `LedgerSource` + `StatementSource`.
- `close_period` — assemble the period's reports and either propose the close for
  sign-off or block with the open items (charter `closePeriod`). Returns a
  `CloseReport` (`READY`/`BLOCKED` + checklist); per §5.7 it writes nothing
  canonical and **never signs** — the human signs. A pure function over the
  reconcile / tax / categorize reports + config + period.
"""

from bookkeeper.skills.categorize import (
    CategorizationReport,
    CategoryFlag,
    CategoryProposal,
    categorize,
)
from bookkeeper.skills.close_period import (
    AssembledPeriod,
    CloseBlocker,
    CloseCheck,
    CloseReport,
    CloseStatus,
    PeriodSummary,
    ProposedClose,
    close_period,
)
from bookkeeper.skills.reconcile import (
    GapKind,
    MatchedPair,
    PairToConfirm,
    ReconciliationGap,
    ReconciliationReport,
    reconcile_account,
)
from bookkeeper.skills.track_tax import (
    HstRegime,
    TargetTax,
    TaxFlag,
    TaxLine,
    TaxRegime,
    TaxSummary,
    UnknownTaxRegime,
    select_regime,
    track_tax,
)

__all__ = [
    "track_tax",
    "TaxSummary",
    "TargetTax",
    "TaxFlag",
    "TaxLine",
    "TaxRegime",
    "HstRegime",
    "select_regime",
    "UnknownTaxRegime",
    "categorize",
    "CategorizationReport",
    "CategoryProposal",
    "CategoryFlag",
    "reconcile_account",
    "ReconciliationReport",
    "ReconciliationGap",
    "MatchedPair",
    "PairToConfirm",
    "GapKind",
    "close_period",
    "CloseReport",
    "CloseStatus",
    "CloseCheck",
    "CloseBlocker",
    "PeriodSummary",
    "AssembledPeriod",
    "ProposedClose",
]
