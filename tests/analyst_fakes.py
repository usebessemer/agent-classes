"""In-memory test substrate for the jr-analyst core — builders + port fakes.

The jr-analyst counterpart to `tests/fakes.py` (the Bookkeeper's substrate),
generalized to the analyst's smaller, **read-only** surface. Two kinds of thing:

- **builders** (`an_actual`, `a_budget_line`, `an_aligned_pair`,
  `an_aligned_dataset`, `make_config`) — construct the frozen model rows, the
  slice-1 `AlignedPair` / `AlignedDataset` a slice-2 skill consumes directly, and a
  live `AnalystConfig` with sensible, *aligned* defaults, so a bare `an_actual()`
  and a bare `a_budget_line()` share the default alignment key
  (`DEFAULT_ALIGN_ON` = `(account, period)`) and pair 1:1 into a ready-made -200.00
  variance with no ceremony. A test overrides only the field it is exercising.
- **fakes** (`FakeActualsSource`, `FakeBudgetSource`) — in-memory implementations of
  the read-only source ports, each recording `self.fetched: list[str]` (the windows
  / periods requested, in order) so a test can prove the skill only ever *read*, and
  read exactly what it should. They mirror the read-side Bookkeeper fakes
  (`FakeLedgerSource` / `FakeStatementSource`): seeded per key, no writer.

Everything here is **in-memory only** — no fixture files, no network, no external
deps, no client names. The concrete adapters that build these rows from a real
system live in the private instance repo; this substrate stands in for them so the
root test suite can exercise the framework core. Not collected by pytest itself
(no `test_` prefix); imported as `from tests.analyst_fakes import ...`.
"""

from __future__ import annotations

from decimal import Decimal

from jr_analyst.config import AnalystConfig
from jr_analyst.model import (
    ActualLine,
    AlignedDataset,
    AlignedPair,
    BudgetLine,
    Certainty,
    UnmappedLine,
)
from jr_analyst.ports import ActualsSource, BudgetSource

# Aligned defaults — a bare actual and a bare budget line share these, so they
# match on the default alignment grain (`(account, period)`) *and* on a finer
# `(account, attribution_target_id, period)` grain (the default budget carries the
# same target rather than a lump `None`). Overriding any one field on one side is
# how a test builds an unaligned / grain-mismatched case.
DEFAULT_ACCOUNT = "6000-marketing"
DEFAULT_PERIOD = "2026-Q2"
DEFAULT_TARGET = "target-001"


def an_actual(
    *,
    account: str = DEFAULT_ACCOUNT,
    attribution_target_id: str = DEFAULT_TARGET,
    period: str = DEFAULT_PERIOD,
    amount: Decimal = Decimal("1000.00"),
    source_ref: str = "actual-src-001",
    certainty: Certainty = Certainty.REALIZED_CLOSED,
) -> ActualLine:
    """Build a realized, attributed, graded `ActualLine` ready to align.

    Defaults are the *aligned* ones: the same `(account, period)` (and target) a
    bare `a_budget_line()` carries, so `an_actual()` and `a_budget_line()` pair 1:1
    out of the box. `attribution_target_id` is never `None` — an actual is always
    attributed upstream (unlike a budget, which may be account-grain). `certainty`
    defaults to the most-certain realized rung; pass `Certainty.REALIZED_OPEN` for
    the in-flight case. `amount` is `Decimal` (exact currency, never `float`).
    """
    return ActualLine(
        account=account,
        attribution_target_id=attribution_target_id,
        period=period,
        amount=amount,
        source_ref=source_ref,
        certainty=certainty,
    )


def a_budget_line(
    *,
    account: str = DEFAULT_ACCOUNT,
    attribution_target_id: str | None = DEFAULT_TARGET,
    period: str = DEFAULT_PERIOD,
    amount: Decimal = Decimal("1200.00"),
    source_ref: str = "budget-src-001",
) -> BudgetLine:
    """Build a `BudgetLine` target, aligned by default to a bare `an_actual()`.

    Shares the actual's default `(account, period)` — and its target — so the two
    align 1:1 with no overrides. Pass `attribution_target_id=None` for the
    account-grain **lump** budget (aligning a lump against an attribution-grain
    actual is a human judgment, so the skill escalates that grain mismatch rather
    than guessing). The default `amount` differs from the actual's so a downstream
    variance is non-zero; `amount` is `Decimal`, never `float`. A budget carries no
    certainty grade — the ladder grades incurred cost, and a budget is a plan.
    """
    return BudgetLine(
        account=account,
        attribution_target_id=attribution_target_id,
        period=period,
        amount=amount,
        source_ref=source_ref,
    )


def an_aligned_pair(
    *,
    actual: ActualLine = an_actual(),
    budget: BudgetLine = a_budget_line(),
) -> AlignedPair:
    """Build a 1:1 `AlignedPair` — a bare call is a ready-made **-200.00** variance.

    The slice-1 output slice 2 consumes directly: `flag_variance` reads
    `AlignedPair`s, not raw ports, so a variance test builds the pair here and
    never re-runs `ingest_and_align`. A bare call pairs a bare `an_actual()`
    (1000.00) with a bare `a_budget_line()` (1200.00) — the two carry the same
    default alignment key — so the signed `actual.amount - budget.amount` delta is
    **-200.00**, an under-budget variance, with zero ceremony. Override one side to
    exercise a different sign or magnitude
    (`actual=an_actual(amount=Decimal("1500.00"))` → +300.00 over-budget). No
    `certainty` kwarg: the pair derives its grade from `actual.certainty` as a
    property, so it can never drift. The default rows are frozen (immutable), so
    reusing the shared default instance across calls is safe.
    """
    return AlignedPair(actual=actual, budget=budget)


def an_aligned_dataset(
    *,
    aligned: tuple[AlignedPair, ...] = (an_aligned_pair(),),
    unmapped: tuple[UnmappedLine, ...] = (),
    window: str = DEFAULT_PERIOD,
) -> AlignedDataset:
    """Build an `AlignedDataset` — a bare call carries one ready-made -200.00 pair.

    The whole slice-1 result, built directly for a slice-2 test with no re-run of
    `ingest_and_align`. A bare call holds a single `an_aligned_pair()` (the -200.00
    under-budget pair) and no `unmapped` lines, over the `DEFAULT_PERIOD` window —
    enough to drive `flag_variance` end to end with nothing to set up. Pass
    `aligned=()` for the empty-dataset case, `aligned=(...)` with several pairs to
    exercise ordering, or `unmapped=(...)` to prove `flag_variance` never reads the
    escalated remainder. `window=` labels the analysis window carried onto the
    result. The default tuples are immutable, so the shared defaults are safe to
    reuse across calls.
    """
    return AlignedDataset(window=window, aligned=aligned, unmapped=unmapped)


def make_config(**overrides: object) -> AnalystConfig:
    """A live, fail-fast-validated `AnalystConfig`; override any field.

    Mirrors `tests/fakes.py::make_config`. The one required field
    (`budget_source_ref`) is supplied generically; every override is threaded
    through `from_mapping` verbatim, so `align_on=(...)` exercises a finer alignment
    grain and `variance_floor=` (a `Decimal`/`str`/`int`, coerced to exact `Decimal`
    on the way through) sets the slice-2 materiality floor — an unset floor stays
    `None` (inert). Overriding `budget_source_ref` with a blank value exercises the
    fail-fast path.
    """
    base: dict[str, object] = dict(budget_source_ref="generic-budget-source")
    base.update(overrides)
    return AnalystConfig.from_mapping(base)


class FakeActualsSource(ActualsSource):
    """In-memory read-side actuals feed: yields the seeded lines for a window.

    Seeded per window (`{window: [ActualLine, ...]}`); a bare `FakeActualsSource()`
    yields nothing for any window. Records every window requested in `self.fetched`
    (in order), so a test can prove the skill read exactly the window(s) it should.
    Read-only by construction — the port has **no writer / store / sink**, so there
    is nothing here to record a write against (the structural §5-style boundary).
    """

    def __init__(self, by_window: dict[str, list[ActualLine]] | None = None):
        self.by_window = {w: list(lines) for w, lines in (by_window or {}).items()}
        self.fetched: list[str] = []  # windows requested, in order

    async def fetch_realized(self, window: str) -> list[ActualLine]:
        self.fetched.append(window)
        return list(self.by_window.get(window, []))


class FakeBudgetSource(BudgetSource):
    """In-memory read-side budget feed: yields the seeded lines for a period.

    The budget counterpart to `FakeActualsSource`. Seeded per period
    (`{period: [BudgetLine, ...]}`); records every period requested in
    `self.fetched` (in order). Read-only, no writer — the analyst never edits the
    budget; a grain mismatch is escalated, never reconciled here.
    """

    def __init__(self, by_period: dict[str, list[BudgetLine]] | None = None):
        self.by_period = {p: list(lines) for p, lines in (by_period or {}).items()}
        self.fetched: list[str] = []  # periods requested, in order

    async def fetch_budget(self, period: str) -> list[BudgetLine]:
        self.fetched.append(period)
        return list(self.by_period.get(period, []))
