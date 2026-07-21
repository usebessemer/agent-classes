"""The jr-analyst data model — the frozen surface for slice 1, vertical-agnostic.

These are the units `ingest_and_align` moves through: a period actual already
graded on the **certainty ladder** (`ActualLine`), a budget target
(`BudgetLine`), the 1:1 alignment of the two (`AlignedPair`), and the escalated
remainder (`UnmappedLine`) — collected into the `AlignedDataset` the skill
returns. Every row is source-agnostic and adapter-free: the concrete
`ActualsSource` / `BudgetSource` adapters that construct these from a real system
live in the private instance repo (see `ports.py`); the framework holds only the
shape.

The certainty ladder is the seam that makes this analyst *forward-looking*: it
grades how settled each figure is rather than treating "actual" as a single
bucket, so slice 1 can see in-flight open costs alongside closed ones. `Decimal`
money throughout (currency is exact; `float` is lossy), everything frozen, and
each row keeps its `source_ref` so every aligned figure and every escalation
links back to the exact source line it came from (charter §1: fully traceable).

Scope note: the ladder's forward-looking rungs (`committed`, `anticipated`) are
defined here so the full grade surface is stable, but their line types
(`CommitmentLine` / `AnticipatedLine`) are deferred to their slice (3–4). Slice 1
carries only realized actuals — `ActualLine`, always graded `realized_closed` or
`realized_open`.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum


class Certainty(str, Enum):
    """How settled a figure is — the forward-looking grade on the ladder.

    A `str` enum (like the Bookkeeper's `RunOutcome` / `GapKind`) so the grade
    serializes to a stable, readable tag for the run log and the review surface.
    Ordered most- to least-certain: the two realized rungs are what slice 1
    aligns; `committed` and `anticipated` are the forward-looking rungs their
    line types (deferred to slice 3–4) will carry.

    The adapter stamps each `ActualLine` via `derive_certainty` (see the
    `certainty.py` slice) before the analyst ever sees it — the framework reads
    the grade, it does not decide it.
    """

    #: A realized cost in a *closed* period — settled and booked; the most
    #: certain figure on the ladder.
    REALIZED_CLOSED = "realized_closed"
    #: A realized cost in the current *open* period — already incurred but not
    #: yet closed; the in-flight figure slice 1 exists to see.
    REALIZED_OPEN = "realized_open"
    #: A committed-but-not-yet-realized cost (e.g. an open PO). Forward-looking;
    #: carried by `CommitmentLine`, deferred to its slice (3–4).
    COMMITTED = "committed"
    #: An anticipated / forecast cost, not yet committed. The least certain rung;
    #: carried by `AnticipatedLine`, deferred to its slice (3–4).
    ANTICIPATED = "anticipated"


@dataclass(frozen=True)
class ActualLine:
    """A period actual, already attributed and graded, ready to align.

    What an `ActualsSource` adapter yields: a realized cost read off the
    Bookkeeper pipeline's output, attributed to a §3 target and stamped with its
    ladder grade. `attribution_target_id` is **never None** — the Bookkeeper
    resolves attribution upstream, so an actual always knows where it belongs
    (unlike a budget, which may be account-grain). In slice 1 `certainty` is one
    of the two realized rungs (`realized_closed` / `realized_open`).

    `amount` is `Decimal` (exact currency, never `float`): alignment matches an
    actual to its budget by key, and downstream variance is computed by exact
    Decimal arithmetic, so a difference is a real variance, not a rounding
    artifact. `source_ref` is the adapter-supplied **stable intake/source id**
    (never `artifact_bytes`, which is `b""` on the read path) linking the figure
    back to its origin for both closed and open lines (charter §1: traceable).
    """

    account: str
    attribution_target_id: str
    period: str
    amount: Decimal
    source_ref: str
    certainty: Certainty


@dataclass(frozen=True)
class BudgetLine:
    """A budget target for a period, to align an actual against.

    What a `BudgetSource` adapter yields. `attribution_target_id` is
    `str | None`: **None means account-grain** — a lump budget set at the account
    level rather than allocated to a specific §3 target. Aligning an
    attribution-grain actual against a lump (None) budget is a human judgment,
    not the skill's, so the grain mismatch is escalated rather than guessed (see
    `ingest_and_align`).

    `amount` is `Decimal` (exact currency, never `float`). `source_ref` links the
    target back to its budget source line for the trail (charter §1: traceable).
    A budget carries no certainty grade — the ladder grades incurred cost, and a
    budget is a plan, not a realized figure.
    """

    account: str
    attribution_target_id: str | None
    period: str
    amount: Decimal
    source_ref: str


@dataclass(frozen=True)
class AlignedPair:
    """One actual aligned 1:1 to the budget target it belongs to.

    The confident output of `ingest_and_align`: an `ActualLine` and the single
    `BudgetLine` it matched on `(account, attribution_target_id, period)`. Strictly
    1:1 — no zero-side pairs and no fabricated `Decimal("0")`; a line with no
    counterpart is escalated as an `UnmappedLine`, never padded into a pair.

    The pair exposes the actual's ladder grade verbatim via `certainty` (a
    property, so it can never drift from `actual.certainty`), which is what keeps
    slice 1 forward-looking: a `realized_open` actual aligns and is graded open,
    it is not flattened to a single "actual" bucket.
    """

    actual: ActualLine
    budget: BudgetLine

    @property
    def certainty(self) -> Certainty:
        """The pair's ladder grade — the actual's, verbatim (never the budget's)."""
        return self.actual.certainty


class UnmappedKind(str, Enum):
    """Why a line could not be aligned 1:1 (exactly one) — the escalation bucket.

    A `str` enum (like the Bookkeeper's `GapKind`) so the reason serializes to a
    stable, readable tag for the review surface. Each bucket is *surfaced*, never
    dropped and never silently resolved: the analyst proposes, a human decides
    (the read-only §5-style boundary).
    """

    #: A realized actual with no matching budget target — spend the plan did not
    #: anticipate.
    UNMATCHED_ACTUAL = "unmatched_actual"
    #: A budget target with no matching actual, or one whose grain cannot be
    #: matched (a lump account-grain budget vs an attribution-grain actual —
    #: allocating it across targets is a human judgment).
    UNMAPPED_BUDGET = "unmapped_budget"
    #: An adapter-surfaced open line with no resolved account — the
    #: capture-completeness signal. Kept, never dropped: a hole in the books is
    #: exactly what a forward-looking analyst must flag.
    UNCATEGORIZED_OPEN = "uncategorized_open"


@dataclass(frozen=True)
class UnmappedLine:
    """A line that did not align 1:1, tagged with why, escalated for review.

    The escalated remainder of `ingest_and_align`: the offending line (an
    `ActualLine` or a `BudgetLine`, per `kind`), its `kind` bucket, and a
    §1-traceable `reason`. Detection-only — surfaced for a human, never
    auto-resolved. The line keeps its own `source_ref` (and, for an actual, its
    `certainty`), so an escalation links back to its source as cleanly as an
    aligned pair does.
    """

    line: ActualLine | BudgetLine
    kind: UnmappedKind
    reason: str


@dataclass(frozen=True)
class AlignedDataset:
    """The full result of `ingest_and_align` over a query window.

    Everything the skill saw for the window, partitioned into the confident
    `aligned` pairs and the escalated `unmapped` lines. The partition is disjoint
    and total: every line the sources yielded lands in exactly one side (count
    conservation), so nothing is silently dropped and nothing is double-counted —
    the guarantee that makes the dataset a trustworthy review surface.

    `window` identifies the analysis window the dataset was built for (the same
    value passed to the skill and to `ActualsSource.fetch_realized`), carried on
    the result so a reviewer sees the scope without re-deriving it. The tuples are
    immutable and in deterministic order for stable, diffable review.
    """

    window: str
    aligned: tuple[AlignedPair, ...]
    unmapped: tuple[UnmappedLine, ...]
