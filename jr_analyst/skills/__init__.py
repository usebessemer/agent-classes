"""The Junior Analyst skills — the charter computation skills, one module each.

A skill is a single framework operation built on the agnostic core (ports +
model + config). Each is vertical-agnostic and adapter-free: it drives ports,
never a concrete system, and — per the read-only jr-analyst boundary — proposes
graded output rather than writing anything canonical.

The v1 build lands here one skill at a time. Built so far:

- `ingest_and_align` — align a window's certainty-tagged realized actuals
  (closed *and* open) to the budget one-to-one and escalate the rest (charter
  `ingestAndAlign`). An async driver over a pure `_align` core; returns an
  `AlignedDataset`, per the read-only boundary it writes nothing canonical.
  Reads via `ActualsSource` + `BudgetSource`; alignment grain from
  `config.align_on`.
- `flag_variance` — the per-pair watchdog: read the aligned pairs and surface
  each material actual-vs-budget variance (signed exact-`Decimal` delta, over/
  under classification) above the materiality floor (charter `flagVariance`,
  §3/§5.4). A pure, **sync** reader (it drives no port); returns a
  `VarianceReport`, proposes-not-writes, non-forecasting.
- `build_report` — the Contract-A assembler: compose an already-computed
  `AlignedDataset` and `VarianceReport` into one PROPOSED `ReportPackage`, plus
  a per-target roll-up (actuals subtotalled per certainty grade, budget referents
  summed separately and never differenced) — charter `buildReport`, §4. Pure and
  **sync**, port-free; assembles-not-writes, and stays on the slice-3 (assembly)
  side of the slice-4 (narrative) line — no ranking, no forecast, no remaining.
  Its inline frozen result model (`ReportPackage` / `TargetRollup` /
  `CertaintySubtotal` / `PackageSummary` / `ReportBasis` / `PackageStatus`)
  re-exports here alongside the operation.
- `explain_variance` — the deterministic narrative substrate (Option A): read a
  composed `ReportPackage` and strike the story `build_report` deferred — per
  target it **differences** the actuals-to-date against the summed referents into
  one signed `target_variance`, classifies its over/under `kind`, and **ranks**
  the target's flagged variances into a full ordered list of `VarianceDriver`s
  (largest `abs(delta)` first), disclosing the sub-floor remainder — charter
  `explainVariance`, §4. Pure and **sync**, port-free; proposes-not-writes,
  `PROPOSED`-only. Deterministic-only: it authors **no** prose (the LLM-narrative
  rung is deferred) and forecasts **nothing** (§2). Its inline frozen result
  model (`VarianceDriver` / `TargetExplanation` / `ExplainedPackage`) re-exports
  here alongside the operation.
"""

from jr_analyst.skills.build_report import (
    CertaintySubtotal,
    PackageStatus,
    PackageSummary,
    ReportBasis,
    ReportPackage,
    TargetRollup,
    build_report,
)
from jr_analyst.skills.explain_variance import (
    ExplainedPackage,
    TargetExplanation,
    VarianceDriver,
    explain_variance,
)
from jr_analyst.skills.flag_variance import (
    VarianceFlag,
    VarianceKind,
    VarianceReport,
    flag_variance,
)
from jr_analyst.skills.ingest_and_align import ingest_and_align

__all__ = [
    "ingest_and_align",
    "flag_variance",
    "VarianceReport",
    "VarianceFlag",
    "VarianceKind",
    "build_report",
    "ReportPackage",
    "TargetRollup",
    "CertaintySubtotal",
    "PackageSummary",
    "ReportBasis",
    "PackageStatus",
    "explain_variance",
    "VarianceDriver",
    "TargetExplanation",
    "ExplainedPackage",
]
