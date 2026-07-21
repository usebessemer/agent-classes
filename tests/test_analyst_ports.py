"""Structural guard for the jr-analyst read-only source ports (`jr_analyst.ports`).

#39/#49 shipped the ports as pure declarations and deferred coverage to #6. This
mirrors `tests/test_contracts.py::test_ports_are_abstract` and adds the class's
**core safety property** as a committed regression guard: the source ports are
abstract, and they expose **no write / store / sink method**. That the analyst
*cannot* write is the read-only §5-style boundary made structural — a later slice
must not be able to bolt a sink onto a source port without this test going red.

`ActualsSource` reads through exactly one method (`fetch_realized`), `BudgetSource`
through exactly one (`fetch_budget`); there is deliberately no third package-level
port (no sink, no contracts module — unlike the Bookkeeper's Contract A/B).
"""

import inspect

import pytest

import jr_analyst
from jr_analyst.ports import ActualsSource, BudgetSource

from tests.analyst_fakes import FakeActualsSource, FakeBudgetSource

# The full read-only port surface — every abstract interface the analyst exposes.
_PORTS = [ActualsSource, BudgetSource]

# Any method name that would imply a canonical write. If one of these ever appears
# on a source port, the read-only boundary has been breached — the guard fails.
_WRITE_NAMES = frozenset(
    {"store", "write", "sink", "save", "put", "persist", "commit", "upsert", "record"}
)

# The single read method each port is allowed to declare.
_READ_METHOD = {ActualsSource: "fetch_realized", BudgetSource: "fetch_budget"}


@pytest.mark.parametrize("port", _PORTS)
def test_source_ports_are_abstract(port):
    """No source port can be instantiated directly — each is an abstract contract."""
    assert inspect.isabstract(port)
    with pytest.raises(TypeError):
        port()


@pytest.mark.parametrize("port", _PORTS)
def test_source_ports_expose_no_write_method(port):
    """The core safety property: a source port has no write / store / sink seam.

    Structural, not conventional — the analyst has no method through which to write
    anything canonical, so the §5-style read-only boundary cannot be forgotten by an
    adapter (there is nothing to forget). Checks the whole method surface, not just
    the abstract set, so a *concrete* write helper would fail this too.
    """
    methods = {
        name for name, _ in inspect.getmembers(port, callable) if not name.startswith("__")
    }
    offending = methods & _WRITE_NAMES
    assert not offending, f"{port.__name__} exposes a write-side method: {sorted(offending)}"


@pytest.mark.parametrize("port", _PORTS)
def test_source_ports_declare_exactly_their_one_read_method(port):
    """Each port's only abstract method is its single read seam — nothing else to implement."""
    assert port.__abstractmethods__ == frozenset({_READ_METHOD[port]})


def test_fakes_are_the_only_concrete_implementations_and_still_have_no_sink():
    """The substrate fakes implement the read seam and inherit the no-sink shape.

    A source port becomes concrete only by implementing its single read method; the
    fakes do exactly that and add no writer — so the read-only boundary holds for the
    test doubles too, not just the abstract declarations.
    """
    actuals = FakeActualsSource()
    budgets = FakeBudgetSource()
    assert isinstance(actuals, ActualsSource)
    assert isinstance(budgets, BudgetSource)
    for fake in (actuals, budgets):
        surface = {name for name in dir(fake) if not name.startswith("__")}
        assert not (surface & _WRITE_NAMES), f"{type(fake).__name__} exposes a write seam"


def test_source_ports_are_on_the_public_surface():
    """Both read-only ports are exported from the package root (`jr_analyst.__all__`)."""
    assert "ActualsSource" in jr_analyst.__all__
    assert "BudgetSource" in jr_analyst.__all__
    assert jr_analyst.ActualsSource is ActualsSource
    assert jr_analyst.BudgetSource is BudgetSource
