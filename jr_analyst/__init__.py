"""The Junior Analyst agent-class framework — the adapter-agnostic core.

The reusable, vertical-agnostic implementation of the jr-analyst FP&A charter: a
forward-looking analyst that ingests period actuals, aligns them against budget,
and grades each alignment on a **certainty ladder** for human review. Like the
Bookkeeper it is an agnostic core driven through ports; a deployment implements
those ports as adapters in its private instance repo and configures the class to
its organization.

jr_analyst ships as a **flat second package inside the `bookkeeper`
distribution** — no separate pyproject, distribution, or version. It shares the
`bookkeeper` release version deliberately (there is no `jr_analyst.__version__`);
the root test suite covers both packages.

The §5-style boundary holds here too: jr_analyst is **read-only** — it reads
actuals and budget through ports and proposes graded alignments; it writes
nothing canonical and exposes no sink. Nothing here is client- or
system-specific. Adapters and instance config live in the private instance repo,
never in this package.

The public surface is populated as the v1 skills land (model, ports, certainty,
config, and the `ingest_and_align` build); the frozen data model and the
read-only source ports are the first slices on it.
"""

from jr_analyst.model import (
    ActualLine,
    AlignedDataset,
    AlignedPair,
    BudgetLine,
    Certainty,
    UnmappedKind,
    UnmappedLine,
)
from jr_analyst.ports import ActualsSource, BudgetSource

__all__ = [
    # data model (the frozen surface for slice 1)
    "Certainty",
    "ActualLine",
    "BudgetLine",
    "AlignedPair",
    "UnmappedKind",
    "UnmappedLine",
    "AlignedDataset",
    # read-only source ports (adapters live in the private instance repo)
    "ActualsSource",
    "BudgetSource",
]
