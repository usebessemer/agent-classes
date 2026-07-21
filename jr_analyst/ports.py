"""Read ports — the seams `ingest_and_align` pulls its inputs through.

The jr-analyst is a **read-only** analyst: it reads the Bookkeeper pipeline's
output and a budget, aligns the two, and grades each alignment on the certainty
ladder for human review. These ports are the only way in, and they are read-only
by construction — there is **no sink port**. The analyst never runs
categorize/close and never writes anything canonical; structurally, it cannot.
That is the §5-style boundary made into code, not a convention (charter §1: the
analyst proposes, a human decides).

The concrete adapters that construct these lines from a real system live in the
private instance repo — never in this framework. An `ActualsSource` adapter runs
the Bookkeeper categorize/close pipeline and stamps each line's ladder grade via
`derive_certainty` (see the `certainty.py` slice) *before* the analyst ever sees
it; the analyst reads the resulting lines only. The framework holds the shape and
the contract; the adapter holds the system.

| port            | reads (charter)             | ladder grade stamped by    |
|-----------------|-----------------------------|----------------------------|
| `ActualsSource` | realized period actuals     | `derive_certainty` (#4)    |
| `BudgetSource`  | budget targets for a period | — (a plan, never graded)   |
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from jr_analyst.model import ActualLine, BudgetLine


class ActualsSource(ABC):
    """Reads realized period actuals off the Bookkeeper pipeline's output (read-only).

    What an adapter yields to `ingest_and_align`: realized costs for the analysis
    window, each already attributed to a §3 target and stamped with its certainty
    ladder grade. The heavy lifting is the adapter's, in the private instance
    repo — it runs the Bookkeeper categorize/close pipeline and stamps each line's
    grade via `derive_certainty` (see the `certainty.py` slice) before returning
    it. The analyst reads the resulting `ActualLine`s only; it never runs
    categorize/close and never decides the grade — it reads the grade the adapter
    stamped.

    Read-only by design. There is deliberately **no writer / store / sink
    method** — the §5-style boundary is structural here, not a policy an adapter
    could forget: the analyst has no seam through which to write. Resolution of
    anything the alignment escalates is always a later, human-gated step, never
    the analyst's.

    Every returned line carries a `source_ref`: the adapter-supplied **stable
    intake/source id** that links the figure back to its origin, for both
    `realized_closed` and `realized_open` lines alike (never `artifact_bytes`,
    which is `b""` on this read path — the analyst totals and aligns figures, it
    does not need the source blob). That id is what keeps every aligned figure and
    every escalation traceable to the exact source line it came from (charter §1).
    """

    @abstractmethod
    async def fetch_realized(self, window: str) -> list[ActualLine]:
        """Return all realized actuals for the analysis `window` (e.g. "2026-Q2").

        Each line is already attributed (`attribution_target_id` never None — the
        Bookkeeper resolves it upstream) and already graded on the certainty
        ladder by the adapter via `derive_certainty`; in slice 1 the grade is one
        of the two realized rungs (`realized_closed` / `realized_open`). `window`
        is the same value the skill carries onto `AlignedDataset.window`, so a
        reviewer sees the scope the dataset was built for without re-deriving it.
        """


class BudgetSource(ABC):
    """Reads the budget targets for a period, to align actuals against (read-only).

    The plan side of the alignment: what a `BudgetSource` adapter yields for a
    period, so `ingest_and_align` can match each realized actual to the budget it
    belongs to. A budget carries **no certainty grade** — the ladder grades
    incurred cost, and a budget is a plan, not a realized figure — which is why
    this port stamps nothing and has no `derive_certainty` step.

    Read-only by design, exactly as `ActualsSource`: there is deliberately **no
    writer / store / sink method**. The analyst never edits the budget; a grain
    mismatch (a lump account-grain budget vs an attribution-grain actual) is
    escalated for a human, never reconciled by the analyst.

    Each returned `BudgetLine` carries a `source_ref` linking the target back to
    its budget source line (never `artifact_bytes`), so an escalated budget line
    is as traceable as an aligned one (charter §1). `attribution_target_id` may be
    `None` — a lump budget set at the account level rather than allocated to a
    specific §3 target.
    """

    @abstractmethod
    async def fetch_budget(self, period: str) -> list[BudgetLine]:
        """Return all budget targets for `period` (e.g. "2026-Q2").

        Lines may be attribution-grain (`attribution_target_id` set) or
        account-grain (`attribution_target_id` None — a lump budget). Aligning an
        attribution-grain actual against a lump budget is a human judgment, so the
        grain mismatch is escalated by `ingest_and_align`, never guessed here.
        """
