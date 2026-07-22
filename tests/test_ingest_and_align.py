"""`ingest_and_align` skill tests — slice-1 alignment under the read-only boundary.

Each test pins one bullet of issue #7's acceptance criteria:

- an async `ingest_and_align(actuals_source, budget_source, config, window)` over a
  pure `_align` core, tested in isolation;
- **1:1** alignment on the configured `align_on` key — the AC's canonical
  `(account, attribution_target_id, period)` grain *and* the conservative default
  `(account, period)`, so the config genuinely drives the grain;
- **both realized rungs flow through** — a `realized_open` actual aligns and its
  pair is graded open, verbatim (`pair.certainty == pair.actual.certainty`): the
  forward-looking guarantee slice 1 exists for;
- **grain mismatch** (a lump `attribution_target_id is None` budget vs an
  attribution-grain actual) → `unmapped_budget`, never guessed;
- an **unmatched actual** (real actual, no budget) → `unmatched_actual`;
- **uncategorized-open** (adapter-surfaced, no account) → `uncategorized_open`,
  never dropped (the capture-completeness signal);
- **no zero-side pairs, no fabricated `Decimal("0")`** — a pair carries both real
  input lines by identity;
- the partition is **disjoint + total** (count conservation), every line keeps its
  `source_ref` + certainty, money stays `Decimal`, the fakes record only reads
  (mutates nothing), the order is deterministic, and no line is double-counted;
- the skill is exported from the package surface.

In-memory fakes only (`FakeActualsSource` / `FakeBudgetSource`); `Decimal` money
throughout, so a preserved amount is exact, never a float artifact.
"""

from decimal import Decimal

import pytest

from jr_analyst.model import (
    AlignedDataset,
    AlignedPair,
    Certainty,
    UnmappedKind,
    UnmappedLine,
)
from jr_analyst.skills.ingest_and_align import _align, ingest_and_align

from tests.analyst_fakes import (
    DEFAULT_PERIOD,
    FakeActualsSource,
    FakeBudgetSource,
    a_budget_line,
    an_actual,
    make_config,
)

# In slice 1 the window *is* the budget period; the substrate's aligned defaults
# key on it, so a bare `an_actual()` / `a_budget_line()` pair here 1:1.
WINDOW = DEFAULT_PERIOD  # "2026-Q2"

# The AC's canonical full grain. The substrate's defaults align on this *and* on
# the coarser default `(account, period)`, so a test can exercise either.
FINE = ("account", "attribution_target_id", "period")


def _sources(actuals, budgets, window=WINDOW):
    """Seed a read-only `FakeActualsSource` / `FakeBudgetSource` for one window."""
    return (
        FakeActualsSource({window: list(actuals)}),
        FakeBudgetSource({window: list(budgets)}),
    )


async def _run(actuals, budgets, *, align_on=None, window=WINDOW):
    """Drive `ingest_and_align` over seeded fakes; return `(dataset, actuals_src, budget_src)`."""
    actuals_src, budget_src = _sources(actuals, budgets, window)
    config = make_config(align_on=align_on) if align_on is not None else make_config()
    dataset = await ingest_and_align(actuals_src, budget_src, config, window)
    return dataset, actuals_src, budget_src


# --- the pure core, tested in isolation --------------------------------------


def test_align_is_a_pure_function_returning_the_partition():
    """`_align` is callable with no I/O and returns `(aligned, unmapped)` lists."""
    aligned, unmapped = _align([an_actual()], [a_budget_line()], FINE)
    assert [type(p) for p in aligned] == [AlignedPair]
    assert unmapped == []


def test_align_mutates_neither_input_list():
    """The pure core reads its inputs; it never mutates the lists it is handed."""
    actuals = [an_actual(), an_actual(account="")]
    budgets = [a_budget_line(), a_budget_line(attribution_target_id=None)]
    before_actuals = list(actuals)
    before_budgets = list(budgets)
    _align(actuals, budgets, FINE)
    assert actuals == before_actuals  # unchanged — order and contents
    assert budgets == before_budgets


# --- 1:1 alignment on the configured grain -----------------------------------


async def test_default_actual_and_budget_align_1to1_on_the_fine_grain():
    """AC headline: align 1:1 on `(account, attribution_target_id, period)`."""
    actual, budget = an_actual(), a_budget_line()
    dataset, _, _ = await _run([actual], [budget], align_on=FINE)

    assert dataset.window == WINDOW  # the scope is carried onto the result
    assert len(dataset.aligned) == 1
    assert dataset.unmapped == ()
    pair = dataset.aligned[0]
    assert pair.actual is actual  # both sides are the real input lines...
    assert pair.budget is budget  # ...matched, not fabricated


async def test_alignment_also_works_on_the_conservative_default_grain():
    """`align_on` unset → the default `(account, period)` grain still pairs the defaults."""
    dataset, _, _ = await _run([an_actual()], [a_budget_line()])  # default align_on
    assert len(dataset.aligned) == 1
    assert dataset.unmapped == ()


async def test_fine_grain_matches_on_attribution_target_not_read_order():
    """On the fine grain the target is part of the key — a same-account budget for a *different* target does not match."""
    actual = an_actual(attribution_target_id="target-A")
    other = a_budget_line(attribution_target_id="target-B")  # same account+period, wrong target
    mine = a_budget_line(attribution_target_id="target-A")
    dataset, _, _ = await _run([actual], [other, mine], align_on=FINE)

    assert len(dataset.aligned) == 1
    assert dataset.aligned[0].budget is mine  # keyed to its own target, not the first-read line
    # The wrong-target budget is left over, surfaced (never silently consumed).
    assert [u.kind for u in dataset.unmapped] == [UnmappedKind.UNMAPPED_BUDGET]
    assert dataset.unmapped[0].line is other


async def test_default_grain_ignores_target_the_instance_did_not_configure():
    """At the coarse default the framework aligns on `(account, period)` only — it never assumes a finer grain the instance did not configure."""
    actual = an_actual(attribution_target_id="target-A")
    budget = a_budget_line(attribution_target_id="target-B")  # differs only on target
    dataset, _, _ = await _run([actual], [budget])  # default (account, period)
    assert len(dataset.aligned) == 1  # coarse grain: target is not part of the identity
    assert dataset.aligned[0].budget is budget


async def test_a_budget_line_is_consumed_at_most_once():
    """1:1: two actuals sharing a key and one budget → one pairs, the other is unmatched (no double-count)."""
    first = an_actual(source_ref="a-1")
    second = an_actual(source_ref="a-2")
    budget = a_budget_line()
    dataset, _, _ = await _run([first, second], [budget], align_on=FINE)

    assert len(dataset.aligned) == 1
    assert dataset.aligned[0].actual is first  # earliest read order wins the single budget
    assert [u.kind for u in dataset.unmapped] == [UnmappedKind.UNMATCHED_ACTUAL]
    assert dataset.unmapped[0].line is second


# --- both realized rungs flow through (the forward-looking guarantee) ---------


@pytest.mark.parametrize(
    "grade", [Certainty.REALIZED_CLOSED, Certainty.REALIZED_OPEN]
)
async def test_both_realized_rungs_align_and_carry_their_grade_verbatim(grade):
    """A `realized_open` actual aligns exactly like a closed one; the pair's grade is the actual's, verbatim."""
    actual = an_actual(certainty=grade)
    dataset, _, _ = await _run([actual], [a_budget_line()], align_on=FINE)

    assert len(dataset.aligned) == 1
    pair = dataset.aligned[0]
    assert pair.certainty is grade  # not flattened to a single "actual" bucket
    assert pair.certainty is pair.actual.certainty  # verbatim; can never drift


async def test_open_and_closed_actuals_flow_through_together():
    """Slice 1 sees in-flight open costs alongside closed ones — both align in one run."""
    closed = an_actual(attribution_target_id="t-closed", certainty=Certainty.REALIZED_CLOSED)
    open_ = an_actual(attribution_target_id="t-open", certainty=Certainty.REALIZED_OPEN)
    budgets = [
        a_budget_line(attribution_target_id="t-closed"),
        a_budget_line(attribution_target_id="t-open"),
    ]
    dataset, _, _ = await _run([closed, open_], budgets, align_on=FINE)

    assert dataset.unmapped == ()
    grades = {p.actual.attribution_target_id: p.certainty for p in dataset.aligned}
    assert grades == {
        "t-closed": Certainty.REALIZED_CLOSED,
        "t-open": Certainty.REALIZED_OPEN,
    }


# --- grain mismatch: a lump budget is escalated, never allocated --------------


async def test_lump_budget_vs_attribution_grain_actual_is_unmapped_budget():
    """AC: a lump (`attribution_target_id is None`) budget vs an attribution-grain actual → `unmapped_budget` — allocating it across jobs is a human judgment."""
    actual = an_actual()
    lump = a_budget_line(attribution_target_id=None)
    dataset, _, _ = await _run([actual], [lump])  # default grain: the key would collide...

    assert dataset.aligned == ()  # ...but the lump is never allocated to the actual
    kinds = {u.line.source_ref: u.kind for u in dataset.unmapped}
    assert kinds == {
        lump.source_ref: UnmappedKind.UNMAPPED_BUDGET,  # grain mismatch, surfaced
        actual.source_ref: UnmappedKind.UNMATCHED_ACTUAL,  # nothing attribution-grain to align to
    }


# --- unmatched actual ---------------------------------------------------------


async def test_actual_with_no_budget_is_unmatched_actual():
    """AC: a real actual with no matching budget target → `unmatched_actual`."""
    actual = an_actual()
    dataset, _, _ = await _run([actual], [], align_on=FINE)

    assert dataset.aligned == ()
    assert [u.kind for u in dataset.unmapped] == [UnmappedKind.UNMATCHED_ACTUAL]
    assert dataset.unmapped[0].line is actual  # the real line, surfaced with its source_ref


async def test_attribution_grain_budget_with_no_actual_is_unmapped_budget():
    """A planned target with nothing realized against it → `unmapped_budget` (no-matching-actual)."""
    budget = a_budget_line()
    dataset, _, _ = await _run([], [budget], align_on=FINE)

    assert dataset.aligned == ()
    assert [u.kind for u in dataset.unmapped] == [UnmappedKind.UNMAPPED_BUDGET]
    assert dataset.unmapped[0].line is budget


# --- uncategorized-open: the capture-completeness signal, never dropped -------


@pytest.mark.parametrize("blank", ["", "   "])
async def test_actual_with_no_account_is_uncategorized_open(blank):
    """AC: an adapter-surfaced open line with no resolved account → `uncategorized_open`, never dropped or matched."""
    orphan = an_actual(account=blank, certainty=Certainty.REALIZED_OPEN)
    # A perfectly good budget is present — the orphan still must not be aligned to it.
    dataset, _, _ = await _run([orphan], [a_budget_line()], align_on=FINE)

    assert dataset.aligned == ()  # never matched despite a candidate budget
    assert [u.kind for u in dataset.unmapped if u.kind is UnmappedKind.UNCATEGORIZED_OPEN]
    orphan_line = next(u.line for u in dataset.unmapped if u.line is orphan)
    assert orphan_line.source_ref == orphan.source_ref  # kept, with its source_ref + grade
    assert orphan_line.certainty is Certainty.REALIZED_OPEN


async def test_uncategorized_open_does_not_consume_a_budget():
    """The orphan is set aside first, so its would-be budget stays free for a real actual."""
    orphan = an_actual(source_ref="orphan", account="")
    good = an_actual(source_ref="good")
    budget = a_budget_line()
    dataset, _, _ = await _run([orphan, good], [budget], align_on=FINE)

    assert len(dataset.aligned) == 1
    assert dataset.aligned[0].actual is good  # the budget went to the real actual
    assert {u.kind for u in dataset.unmapped} == {UnmappedKind.UNCATEGORIZED_OPEN}


# --- no zero-side pairs, no fabricated Decimal("0") --------------------------


async def test_no_zero_side_pairs_are_fabricated():
    """A one-sided line is escalated, never padded into a pair with a fabricated zero counterpart."""
    actual = an_actual(source_ref="lonely-actual")
    budget = a_budget_line(source_ref="lonely-budget", attribution_target_id="other-target")
    dataset, _, _ = await _run([actual], [budget], align_on=FINE)

    assert dataset.aligned == ()  # neither side padded into a pair
    # Every aligned pair (there are none here, but the invariant holds generally)
    # carries two real input lines by identity — nothing is a synthesized zero.
    for pair in dataset.aligned:
        assert isinstance(pair.actual.amount, Decimal)
        assert isinstance(pair.budget.amount, Decimal)
    assert {u.line.source_ref for u in dataset.unmapped} == {"lonely-actual", "lonely-budget"}


# --- partition: disjoint + total, no double-count, order deterministic --------


def _mixed():
    """A dataset touching every branch: a clean pair, an unmatched actual, an orphan, a lone budget, a lump budget."""
    paired_actual = an_actual(source_ref="paired-actual", attribution_target_id="t-paired")
    paired_budget = a_budget_line(source_ref="paired-budget", attribution_target_id="t-paired")
    unmatched_actual = an_actual(source_ref="unmatched-actual", attribution_target_id="t-orphan-actual")
    orphan_open = an_actual(source_ref="orphan-open", account="")
    lone_budget = a_budget_line(source_ref="lone-budget", attribution_target_id="t-lone")
    lump_budget = a_budget_line(source_ref="lump-budget", attribution_target_id=None)
    actuals = [paired_actual, unmatched_actual, orphan_open]
    budgets = [paired_budget, lone_budget, lump_budget]
    return actuals, budgets


async def test_partition_is_disjoint_and_total_count_conserved():
    """AC: every actual and budget lands in exactly one side — count conservation, no double-count."""
    actuals, budgets = _mixed()
    dataset, _, _ = await _run(actuals, budgets, align_on=FINE)

    # Count conservation: 2 per pair + 1 per unmapped == total inputs.
    total = len(actuals) + len(budgets)
    assert 2 * len(dataset.aligned) + len(dataset.unmapped) == total

    # Disjoint + total by identity: every input object appears exactly once.
    seen = [p.actual for p in dataset.aligned]
    seen += [p.budget for p in dataset.aligned]
    seen += [u.line for u in dataset.unmapped]
    input_ids = {id(x) for x in actuals + budgets}
    assert {id(x) for x in seen} == input_ids  # nothing missing, nothing foreign
    assert len(seen) == len(input_ids)  # nothing double-counted

    # The buckets landed as designed.
    assert {u.kind for u in dataset.unmapped} == {
        UnmappedKind.UNMATCHED_ACTUAL,
        UnmappedKind.UNCATEGORIZED_OPEN,
        UnmappedKind.UNMAPPED_BUDGET,
    }


async def test_every_line_keeps_its_source_ref_and_certainty():
    """AC: an aligned or escalated line keeps its own `source_ref` (and, for an actual, its grade)."""
    actuals, budgets = _mixed()
    dataset, _, _ = await _run(actuals, budgets, align_on=FINE)

    pair = dataset.aligned[0]
    assert pair.actual.source_ref == "paired-actual"
    assert pair.budget.source_ref == "paired-budget"
    assert pair.certainty is pair.actual.certainty

    by_ref = {u.line.source_ref: u for u in dataset.unmapped}
    # Each escalated line is the real input, so its source_ref (and an actual's
    # certainty) survive verbatim onto the review surface.
    assert by_ref["unmatched-actual"].line.certainty is Certainty.REALIZED_CLOSED
    assert by_ref["orphan-open"].kind is UnmappedKind.UNCATEGORIZED_OPEN
    assert by_ref["lump-budget"].kind is UnmappedKind.UNMAPPED_BUDGET


async def test_order_is_deterministic_and_grouped():
    """AC: deterministic order — aligned in actual read order; unmapped grouped by kind, stable across runs."""
    actuals, budgets = _mixed()
    first, _, _ = await _run(actuals, budgets, align_on=FINE)
    second, _, _ = await _run(actuals, budgets, align_on=FINE)

    assert first == second  # frozen dataclasses compare by value — fully reproducible
    # Unmapped groups by kind: unmatched_actual, then uncategorized_open, then the
    # two unmapped_budgets (the lone attribution-grain target, then the lump) in
    # budget read order.
    assert [u.kind for u in first.unmapped] == [
        UnmappedKind.UNMATCHED_ACTUAL,
        UnmappedKind.UNCATEGORIZED_OPEN,
        UnmappedKind.UNMAPPED_BUDGET,
        UnmappedKind.UNMAPPED_BUDGET,
    ]
    # ...and within that group, budget read order is preserved (lone before lump).
    assert [u.line.source_ref for u in first.unmapped if u.kind is UnmappedKind.UNMAPPED_BUDGET] == [
        "lone-budget",
        "lump-budget",
    ]


async def test_amounts_stay_decimal_through_alignment():
    """AC: Decimal-only — an aligned line's amounts are exact `Decimal`, never coerced to float."""
    actuals, budgets = _mixed()
    dataset, _, _ = await _run(actuals, budgets, align_on=FINE)
    pair = dataset.aligned[0]
    assert isinstance(pair.actual.amount, Decimal)
    assert isinstance(pair.budget.amount, Decimal)
    assert not isinstance(pair.actual.amount, float)


# --- read-only: reads exactly the window, records only reads -----------------


async def test_reads_both_sources_for_the_window_and_records_only_reads():
    """The skill reads each source once for the window and mutates nothing (fakes record reads only)."""
    actuals, budgets = _mixed()
    dataset, actuals_src, budget_src = await _run(actuals, budgets, align_on=FINE)

    assert isinstance(dataset, AlignedDataset)
    assert actuals_src.fetched == [WINDOW]  # read exactly the window asked for...
    assert budget_src.fetched == [WINDOW]
    # ...and there is nowhere to write: the ports expose no writer / store / sink,
    # so `fetched` records the *only* interactions the skill could have.
    assert not hasattr(actuals_src, "store")
    assert not hasattr(budget_src, "store")


async def test_empty_window_returns_an_empty_partition():
    """No actuals and no budgets → an empty, well-formed dataset carrying the window."""
    dataset, actuals_src, budget_src = await _run([], [], align_on=FINE)
    assert dataset == AlignedDataset(window=WINDOW, aligned=(), unmapped=())
    assert actuals_src.fetched == [WINDOW]
    assert budget_src.fetched == [WINDOW]


# --- exported on the package surface -----------------------------------------


def test_ingest_and_align_is_exported_from_the_package():
    """The skill is on both the package and the skills-subpackage public surface."""
    import jr_analyst
    from jr_analyst.skills import ingest_and_align as from_skills

    assert jr_analyst.ingest_and_align is ingest_and_align
    assert from_skills is ingest_and_align
    assert "ingest_and_align" in jr_analyst.__all__
