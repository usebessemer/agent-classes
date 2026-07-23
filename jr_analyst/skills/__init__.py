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
"""

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
]
