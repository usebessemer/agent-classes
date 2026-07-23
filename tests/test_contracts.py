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


def test_package_writer_is_an_abstract_write_side_stub():
    """Contract A is interface-only: the write-side `write_package(package)` is abstract.

    Refined per Task 6: the write-side takes the *assembled* package (not a bare
    `period`) — assembly is the `generateAccountantPackage` skill; this port is the
    gated, instance-side publish step that renders it (§5.4).
    """
    assert inspect.isabstract(PackageWriter)
    assert "write_package" in PackageWriter.__abstractmethods__
    # The old assemble-side stub is gone — assembly moved to the skill.
    assert not hasattr(PackageWriter, "generate_package")
    # The write-side takes the assembled package, not a raw period string.
    params = list(inspect.signature(PackageWriter.write_package).parameters)
    assert params == ["self", "package"]


def test_public_surface_is_importable():
    """Everything in __all__ resolves — `import bookkeeper` exposes the full core."""
    for name in bookkeeper.__all__:
        assert hasattr(bookkeeper, name), f"missing from package surface: {name}"
    assert bookkeeper.__version__ == "0.3.0"
