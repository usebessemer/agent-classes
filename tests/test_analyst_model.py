"""Model invariants for the jr-analyst frozen data surface (`jr_analyst.model`).

#38/#48 shipped the model as pure, mypy-strict-clean declarations and deferred
coverage to this substrate issue (#6). These pin the load-bearing invariants the
later skill relies on and a refactor could silently break:

- the `Certainty` / `UnmappedKind` **exact value sets** (a stray or renamed rung
  would break the ladder and the review tags);
- every row is **frozen** (mutation raises `FrozenInstanceError`) — the model is a
  read-only surface;
- money is **`Decimal`, never `float`** (exact currency; a float variance is a
  rounding artifact, not a real difference);
- `AlignedPair.certainty` is a **property equal to `actual.certainty`** that cannot
  be set at construction — the pair exposes the actual's grade verbatim and it can
  never drift.
"""

from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from jr_analyst.model import (
    ActualLine,
    AlignedDataset,
    AlignedPair,
    BudgetLine,
    Certainty,
    UnmappedKind,
    UnmappedLine,
)

from tests.analyst_fakes import a_budget_line, an_actual


# --- enums: exact value sets, stable str tags --------------------------------


def test_certainty_is_a_str_enum_with_the_exact_ladder():
    """The four rungs, most- to least-certain — no more, no fewer, values pinned."""
    assert issubclass(Certainty, str)
    assert {c.value for c in Certainty} == {
        "realized_closed",
        "realized_open",
        "committed",
        "anticipated",
    }
    # The tag serializes to its string value for the run log / review surface.
    assert Certainty.REALIZED_OPEN.value == "realized_open"


def test_unmapped_kind_is_a_str_enum_with_the_exact_buckets():
    """The three escalation buckets — pinned so a renamed tag can't slip through."""
    assert issubclass(UnmappedKind, str)
    assert {k.value for k in UnmappedKind} == {
        "unmatched_actual",
        "unmapped_budget",
        "uncategorized_open",
    }


# --- every row is frozen -----------------------------------------------------


def test_actual_line_is_frozen():
    with pytest.raises(FrozenInstanceError):
        an_actual().amount = Decimal("2.00")  # type: ignore[misc]


def test_budget_line_is_frozen():
    with pytest.raises(FrozenInstanceError):
        a_budget_line().amount = Decimal("2.00")  # type: ignore[misc]


def test_aligned_pair_is_frozen():
    pair = AlignedPair(actual=an_actual(), budget=a_budget_line())
    with pytest.raises(FrozenInstanceError):
        pair.actual = an_actual()  # type: ignore[misc]


def test_unmapped_line_is_frozen():
    line = UnmappedLine(
        line=an_actual(),
        kind=UnmappedKind.UNMATCHED_ACTUAL,
        reason="no matching budget target",
    )
    with pytest.raises(FrozenInstanceError):
        line.reason = "changed"  # type: ignore[misc]


def test_aligned_dataset_is_frozen():
    ds = AlignedDataset(window="2026-Q2", aligned=(), unmapped=())
    with pytest.raises(FrozenInstanceError):
        ds.window = "2026-Q3"  # type: ignore[misc]


# --- money is Decimal, never float -------------------------------------------


def test_actual_and_budget_amounts_are_decimal_not_float():
    assert isinstance(an_actual().amount, Decimal)
    assert isinstance(a_budget_line().amount, Decimal)
    # Decimal is not a float subclass — the exactness guarantee holds structurally.
    assert not isinstance(an_actual().amount, float)
    assert not isinstance(a_budget_line().amount, float)


# --- AlignedPair.certainty: a property mirroring the actual, never settable ---


def test_aligned_pair_certainty_is_a_property():
    assert isinstance(AlignedPair.certainty, property)


@pytest.mark.parametrize(
    "grade", [Certainty.REALIZED_CLOSED, Certainty.REALIZED_OPEN]
)
def test_aligned_pair_certainty_equals_the_actuals(grade):
    """The pair's grade is the actual's, verbatim — a `realized_open` actual stays open."""
    pair = AlignedPair(actual=an_actual(certainty=grade), budget=a_budget_line())
    assert pair.certainty is grade
    assert pair.certainty is pair.actual.certainty  # never the budget's; can't drift


def test_aligned_pair_certainty_cannot_be_set_at_construction():
    """`certainty` is a derived property, not a field — passing it is a TypeError."""
    with pytest.raises(TypeError):
        AlignedPair(  # type: ignore[call-arg]
            actual=an_actual(),
            budget=a_budget_line(),
            certainty=Certainty.REALIZED_OPEN,
        )


def test_aligned_pair_certainty_cannot_be_assigned():
    """The property has no setter, so assigning to it raises (never silently drifts)."""
    pair = AlignedPair(actual=an_actual(), budget=a_budget_line())
    with pytest.raises(AttributeError):
        pair.certainty = Certainty.REALIZED_OPEN  # type: ignore[misc]
