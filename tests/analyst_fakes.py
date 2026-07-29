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
  Slice 3 layers roll-up builders on these — `a_target_pair` (one pair under a
  named attribution target), `a_multi_target_dataset` / `a_multi_grade_pairs` /
  `a_cross_target_budget_pair`, and the `a_variance_flag` / `some_variance_flags`
  / `a_variance_report` set that hand-builds a coherent `VarianceReport` for a
  `build_report` test *without* re-running `flag_variance` — all reusing these
  defaults, never changing the bare -200.00 builders. Slice 4 layers the
  composed-package builders — `a_report_package` (a *real* `ReportPackage` run
  through the shipped `build_report`, never a hand-forged dataclass),
  `a_coverage_mix_package` (an over-floor flagged pair + a sub-floor unflagged one,
  so a `subfloor_remainder` is non-zero), and `a_mixed_grade_multi_target_package`
  (two targets, one carrying two grades) — on the slice-3 builders, still untouched.
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

from collections.abc import Sequence
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
from jr_analyst.skills.build_report import ReportPackage, build_report
from jr_analyst.skills.flag_variance import (
    VarianceFlag,
    VarianceKind,
    VarianceReport,
)

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


# --- slice-3 roll-up substrate: multi-target / multi-grade / cross-target -----
# The builders a per-target roll-up (`build_report`, slice 3) test needs, layered
# on the slice-1/-2 defaults above without touching the bare -200.00 builders. All
# in-memory, generic accounts and `attribution_target_id`s (the generalized shape),
# no client names — the same public-cleanliness contract the whole substrate keeps.

#: A second generic attribution target, so a dataset can span ≥2 distinct targets
#: (`DEFAULT_TARGET` is the first). Both are opaque, client-agnostic ids.
SECOND_TARGET = "target-002"

# Decimal zero for the sign classification in `a_variance_flag` (never `float`, so
# the compare never mixes `Decimal` and `float`), mirroring `flag_variance._ZERO`.
_ZERO = Decimal("0")


def a_target_pair(
    target: str,
    *,
    tag: str = "",
    actual_amount: Decimal = Decimal("1500.00"),
    budget_amount: Decimal = Decimal("1200.00"),
    certainty: Certainty = Certainty.REALIZED_CLOSED,
    account: str = DEFAULT_ACCOUNT,
    period: str = DEFAULT_PERIOD,
) -> AlignedPair:
    """A 1:1 `AlignedPair` under one attribution `target`, both sides carrying it.

    The per-target building block the multi-target / multi-grade helpers compose.
    Both the actual and the budget carry `target` (so the pair aligns on the finer
    `(account, attribution_target_id, period)` grain as well as the coarse default),
    and each carries a `source_ref` namespaced by `target` — and by `tag` when given
    — so every pair in a multi-pair dataset stays individually traceable. The
    default amounts are a +300.00 over-budget variance; override either side for a
    different sign or magnitude, or `certainty=` for the open grade.
    """
    suffix = f"-{tag}" if tag else ""
    return an_aligned_pair(
        actual=an_actual(
            account=account,
            attribution_target_id=target,
            period=period,
            amount=actual_amount,
            source_ref=f"actual-{target}{suffix}",
            certainty=certainty,
        ),
        budget=a_budget_line(
            account=account,
            attribution_target_id=target,
            period=period,
            amount=budget_amount,
            source_ref=f"budget-{target}{suffix}",
        ),
    )


def a_multi_target_dataset(
    *,
    targets: tuple[str, ...] = (DEFAULT_TARGET, SECOND_TARGET),
    budget_amount: Decimal = Decimal("1000.00"),
    window: str = DEFAULT_PERIOD,
) -> AlignedDataset:
    """An `AlignedDataset` spanning ≥2 distinct `actual.attribution_target_id`s.

    One `a_target_pair` per entry in `targets` (default two), each with its own
    target *and* its own `source_ref` — so a per-target roll-up test can group the
    flags by `pair.actual.attribution_target_id` and prove the groups stay distinct.
    Each target gets a distinct over-budget magnitude (target *i* is `+100*(i+1)`
    over `budget_amount`), so per-target roll-up sums differ and a merged-vs-grouped
    bug is visible. `window` labels the analysis window carried onto the dataset.
    """
    pairs = tuple(
        a_target_pair(
            target,
            actual_amount=budget_amount + Decimal(100 * (index + 1)),
            budget_amount=budget_amount,
        )
        for index, target in enumerate(targets)
    )
    return an_aligned_dataset(aligned=pairs, window=window)


def a_multi_grade_pairs(
    *,
    target: str = DEFAULT_TARGET,
    closed_actual: Decimal = Decimal("1500.00"),
    open_actual: Decimal = Decimal("400.00"),
    budget_amount: Decimal = Decimal("1200.00"),
    account: str = DEFAULT_ACCOUNT,
    period: str = DEFAULT_PERIOD,
) -> tuple[AlignedPair, AlignedPair]:
    """Two pairs under the SAME `target`, graded `realized_closed` and `realized_open`.

    The substrate for grade-separation: a per-target roll-up must not blindly sum a
    closed variance and an open one into a single ungraded figure — the ladder grade
    rides through. Both pairs share `target` (and account/period) but carry
    grade-tagged `source_ref`s so the two rows stay distinguishable. The closed pair
    is a +300.00 over-budget variance, the open one a -800.00 under-budget
    (actuals-to-date) variance, so a naive cross-grade sum (-500.00) is visibly
    wrong against the grade-separated pair.
    """
    closed = a_target_pair(
        target,
        tag="closed",
        actual_amount=closed_actual,
        budget_amount=budget_amount,
        certainty=Certainty.REALIZED_CLOSED,
        account=account,
        period=period,
    )
    open_pair = a_target_pair(
        target,
        tag="open",
        actual_amount=open_actual,
        budget_amount=budget_amount,
        certainty=Certainty.REALIZED_OPEN,
        account=account,
        period=period,
    )
    return (closed, open_pair)


def a_cross_target_budget_pair(
    *,
    actual_target: str = DEFAULT_TARGET,
    budget_target: str = SECOND_TARGET,
    account: str = DEFAULT_ACCOUNT,
    period: str = DEFAULT_PERIOD,
    actual_amount: Decimal = Decimal("1500.00"),
    budget_amount: Decimal = Decimal("1200.00"),
) -> AlignedPair:
    """A pair whose budget carries a DIFFERENT target than its actual — the grouping-dimension trap.

    Under the conservative default `align_on=('account','period')` this pair aligns
    1:1 (both sides share account + period) even though
    `budget.attribution_target_id` (`budget_target`) differs from
    `actual.attribution_target_id` (`actual_target`). A per-target roll-up keyed off
    the actual's target must not assume the budget agrees on that dimension — on the
    finer `(account, attribution_target_id, period)` grain this same pair would not
    align at all. `actual_target` and `budget_target` must differ (asserted).
    """
    assert actual_target != budget_target, "a cross-target pair needs two distinct targets"
    return an_aligned_pair(
        actual=an_actual(
            account=account,
            attribution_target_id=actual_target,
            period=period,
            amount=actual_amount,
            source_ref=f"actual-{actual_target}",
        ),
        budget=a_budget_line(
            account=account,
            attribution_target_id=budget_target,
            period=period,
            amount=budget_amount,
            source_ref=f"budget-{budget_target}",
        ),
    )


def _flag_reason(pair: AlignedPair, delta: Decimal, kind: VarianceKind) -> str:
    """A §1-traceable fixture reason — both `source_ref`s, both amounts, the delta, the grade.

    Mirrors the shape of `flag_variance`'s own reason without importing that skill's
    private helper, so the substrate stays self-contained yet a `build_report` test
    inspecting a flag's `reason` sees the same traceable fields a real run carries.
    """
    direction = "over" if kind is VarianceKind.OVER_BUDGET else "under"
    return (
        f"Actual {pair.actual.source_ref!r} ({pair.actual.amount}, grade "
        f"{pair.certainty.value}) is {direction} budget {pair.budget.source_ref!r} "
        f"({pair.budget.amount}) by {delta} (test-substrate fixture)."
    )


def a_variance_flag(
    pair: AlignedPair,
    *,
    kind: VarianceKind | None = None,
    delta: Decimal | None = None,
    certainty: Certainty | None = None,
    reason: str | None = None,
) -> VarianceFlag:
    """One `VarianceFlag`, coherent with `pair` by default — a `build_report` input without the skill.

    Derives the signed exact-`Decimal` delta (`actual.amount - budget.amount`), the
    over/under `kind` from its sign, and the `certainty` from `pair.certainty`
    verbatim — the same coherence `flag_variance` produces, hand-built so a
    `build_report` test never re-runs the slice-2 skill. Any field can be overridden
    to construct a deliberately-inconsistent flag; `reason` defaults to the
    traceable `_flag_reason`. A zero-delta pair has no variance to flag, so an
    unclassifiable call (delta 0, no explicit `kind`) raises rather than guessing a
    side — a real `VarianceFlag` is always a surfaced *non-zero* variance.
    """
    signed = (pair.actual.amount - pair.budget.amount) if delta is None else delta
    if kind is None:
        if signed == _ZERO:
            raise ValueError(
                "a zero-delta pair has no variance to flag — pass an explicit kind "
                "or a non-zero pair (a real VarianceFlag is always a surfaced "
                "non-zero variance)"
            )
        resolved_kind = (
            VarianceKind.OVER_BUDGET if signed > _ZERO else VarianceKind.UNDER_BUDGET
        )
    else:
        resolved_kind = kind
    resolved_certainty = certainty if certainty is not None else pair.certainty
    resolved_reason = (
        reason if reason is not None else _flag_reason(pair, signed, resolved_kind)
    )
    return VarianceFlag(
        kind=resolved_kind,
        delta=signed,
        certainty=resolved_certainty,
        reason=resolved_reason,
        pair=pair,
    )


def some_variance_flags(pairs: Sequence[AlignedPair]) -> tuple[VarianceFlag, ...]:
    """One coherent `VarianceFlag` per pair, in the given order — no grouping, no floor.

    A thin fan of `a_variance_flag` over `pairs`: the caller controls which pairs
    become flags and in what order, so a `build_report` test assembles exactly the
    report it needs. Unlike `flag_variance` this applies no materiality floor and
    skips no pair — it is a fixture, not the skill (so every pair must carry a
    non-zero variance, per `a_variance_flag`).
    """
    return tuple(a_variance_flag(pair) for pair in pairs)


def a_variance_report(
    *,
    pairs: Sequence[AlignedPair] | None = None,
    flags: Sequence[VarianceFlag] | None = None,
    window: str = DEFAULT_PERIOD,
) -> VarianceReport:
    """A coherent `VarianceReport` for a `build_report` test — assembled without running `flag_variance`.

    Pass `pairs` to derive one coherent flag per pair (via `some_variance_flags`),
    or `flags` to wrap hand-built flags verbatim; supplying both is a caller error.
    A bare call wraps the single bare -200.00 pair. `window` labels the analysis
    window carried onto the report, exactly as `flag_variance` carries
    `dataset.window` — the field `build_report` reads back for its package scope.
    """
    if pairs is not None and flags is not None:
        raise ValueError("pass either pairs or flags, not both")
    resolved = (
        flags
        if flags is not None
        else some_variance_flags(pairs if pairs is not None else (an_aligned_pair(),))
    )
    return VarianceReport(window=window, flags=tuple(resolved))


# --- slice-4 substrate: composed `ReportPackage` fixtures --------------------
# The builders a slice-4 (`explain_variance`) test needs: a *real* `ReportPackage`
# composed by the shipped `build_report` (never a hand-forged dataclass, so the
# fixtures track the true slice-3 shape — `rollup`, `variances`, `escalations`,
# `status=PROPOSED`), plus the two scenarios slice 4 exercises — a coverage-mix
# target (an over-floor flagged pair + a sub-floor *unflagged* pair, so a
# `subfloor_remainder` is non-zero) and a mixed-grade multi-target package (so
# ranking and per-target grouping have something to bite on). All layered on the
# slice-1/-2/-3 builders above; the bare -200.00 builders stay untouched.

#: The nominal materiality floor the coverage-mix amounts straddle: the over-floor
#: pair's `abs(delta)` clears it and the sub-floor pair's does not, so running the
#: shipped `flag_variance` at this floor surfaces **exactly** the over-floor pair
#: (proven in `test_analyst_fakes.py`). The composed package does not carry the
#: floor — the hand-built report flags the over-floor pair directly — so this is a
#: coherence anchor, not a field on the result.
COVERAGE_MIX_FLOOR = Decimal("100.00")


def a_report_package(
    *,
    dataset: AlignedDataset | None = None,
    variance_report: VarianceReport | None = None,
    config: AnalystConfig | None = None,
) -> ReportPackage:
    """A real `ReportPackage` composed by the shipped `build_report` — no re-implemented roll-up.

    The slice-4 counterpart to slice-3's `a_variance_report`: where that hand-builds
    a coherent `VarianceReport` *without* running `flag_variance`, this composes a
    real `ReportPackage` *with* the shipped `build_report`, so an `explain_variance`
    test gets a package input that tracks the true slice-3 shape (`rollup`,
    `variances`, `escalations`, `status=PackageStatus.PROPOSED`) rather than a
    hand-forged dataclass that could drift from it. A bare call composes the single
    -200.00 pair (one under-budget flag) into a one-target package. Pass `dataset` /
    `variance_report` for a bespoke package; when `variance_report` is omitted it is
    derived from the dataset's own pairs *and window*, so the two always describe the
    same analysis window (`build_report` fails fast on a mismatch). `config` defaults
    to `make_config()` — its `align_on` rides onto the package as grain metadata.
    """
    resolved_dataset = dataset if dataset is not None else an_aligned_dataset()
    resolved_report = (
        variance_report
        if variance_report is not None
        else a_variance_report(
            pairs=resolved_dataset.aligned, window=resolved_dataset.window
        )
    )
    resolved_config = config if config is not None else make_config()
    return build_report(resolved_dataset, resolved_report, resolved_config)


def a_coverage_mix_package(
    *,
    target: str = DEFAULT_TARGET,
    over_floor_actual: Decimal = Decimal("1500.00"),
    over_floor_budget: Decimal = Decimal("1200.00"),
    subfloor_actual: Decimal = Decimal("1050.00"),
    subfloor_budget: Decimal = Decimal("1000.00"),
    window: str = DEFAULT_PERIOD,
    config: AnalystConfig | None = None,
) -> ReportPackage:
    """A one-target package whose roll-up carries BOTH an over- and a sub-floor pair, only the first flagged.

    The coverage-mix scenario a `subfloor_remainder` test needs: a single `target`
    carrying two aligned pairs — an over-floor pair (default +300.00, `abs(delta)`
    over `COVERAGE_MIX_FLOOR`) that **is** flagged, and a sub-floor pair (default
    +50.00, below the floor but non-zero) that is **not**. Both land in the same
    target's roll-up (`len(rollup.pairs) == 2`, since the roll-up groups *every*
    aligned pair), but `package.variances` carries only the over-floor flag — built
    via `a_variance_report(flags=some_variance_flags((over_floor_pair,)))` — so a
    downstream `subfloor_remainder` is **non-zero** and equals the unflagged pair's
    signed delta. Both pairs come from `a_target_pair` (distinct `tag`s → distinct
    `source_ref`s); override the amounts to move either side of the floor.
    """
    over_floor_pair = a_target_pair(
        target,
        tag="over",
        actual_amount=over_floor_actual,
        budget_amount=over_floor_budget,
    )
    subfloor_pair = a_target_pair(
        target,
        tag="sub",
        actual_amount=subfloor_actual,
        budget_amount=subfloor_budget,
    )
    dataset = an_aligned_dataset(aligned=(over_floor_pair, subfloor_pair), window=window)
    variance_report = a_variance_report(
        flags=some_variance_flags((over_floor_pair,)), window=window
    )
    return a_report_package(
        dataset=dataset, variance_report=variance_report, config=config
    )


def a_mixed_grade_multi_target_package(
    *,
    window: str = DEFAULT_PERIOD,
    config: AnalystConfig | None = None,
) -> ReportPackage:
    """A package spanning two targets, one carrying two certainty grades — ranking + grouping fodder.

    The scenario slice-4 ranking and per-target grouping need: the first target
    (`DEFAULT_TARGET`) carries the two `a_multi_grade_pairs` rows — a
    `realized_closed` +300.00 and a `realized_open` -800.00 — so a per-target driver
    ranking has two distinct grades *and* two distinct magnitudes to order (open
    `abs 800` ranks over closed `abs 300`); the second target (`SECOND_TARGET`)
    carries the multi-target dataset's own +200.00 pair, so grouping spans two targets
    with a third distinct magnitude. Every pair is flagged (`some_variance_flags`), so
    the `variances` cover all three and none fall sub-floor — the companion to
    `a_coverage_mix_package`.
    """
    closed, open_pair = a_multi_grade_pairs(target=DEFAULT_TARGET)
    second_target_pairs = tuple(
        pair
        for pair in a_multi_target_dataset(window=window).aligned
        if pair.actual.attribution_target_id == SECOND_TARGET
    )
    pairs = (closed, open_pair) + second_target_pairs
    dataset = an_aligned_dataset(aligned=pairs, window=window)
    variance_report = a_variance_report(flags=some_variance_flags(pairs), window=window)
    return a_report_package(
        dataset=dataset, variance_report=variance_report, config=config
    )


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
