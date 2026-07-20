"""Guard test pinning the package marker to the single source of truth.

`pyproject.toml`'s `project.version` is the release version (it drives the built
artifact and the git tag). `bookkeeper.__version__` is the runtime marker. These
must agree — a bump that touches one side but not the other is the silent drift
this test fails loudly on (the exact miss that left `__version__` stale at 0.1.0
through the v0.2.0 cut).
"""

import tomllib
from pathlib import Path

import bookkeeper


def test_version_matches_pyproject():
    pyproject = tomllib.loads(
        (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text()
    )
    assert bookkeeper.__version__ == pyproject["project"]["version"]
