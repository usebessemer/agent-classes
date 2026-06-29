"""Tests for the contracts/ports surface.

Pins the structural AC: every port and contract is an ABC (no concrete
implementation in the package), the public import surface is clean, and the
Contract A `PackageWriter` is defined as an abstract stub.
"""

import inspect

import pytest

import bookkeeper
from bookkeeper.contracts import Notifier, PackageWriter, ReviewQueue, RunLog
from bookkeeper.ports import (
    AttributionResolver,
    Extractor,
    IntakeSource,
    LedgerSink,
)

# Every abstract interface the framework exposes.
_ABCS = [
    IntakeSource,
    Extractor,
    AttributionResolver,
    LedgerSink,
    ReviewQueue,
    RunLog,
    Notifier,
    PackageWriter,
]


@pytest.mark.parametrize("abc", _ABCS)
def test_ports_are_abstract(abc):
    """No port can be instantiated directly — each is an abstract contract."""
    with pytest.raises(TypeError):
        abc()


def test_package_writer_is_an_abstract_stub():
    """Contract A is interface-only: `generate_package` is abstract, unimplemented."""
    assert inspect.isabstract(PackageWriter)
    assert "generate_package" in PackageWriter.__abstractmethods__


def test_public_surface_is_importable():
    """Everything in __all__ resolves — `import bookkeeper` exposes the full core."""
    for name in bookkeeper.__all__:
        assert hasattr(bookkeeper, name), f"missing from package surface: {name}"
    assert bookkeeper.__version__ == "0.1.0"
