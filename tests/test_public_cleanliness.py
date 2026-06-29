"""Public-cleanliness hard gate (charter / issue AC).

This repo is public MIT. The framework must be generic only — no client data,
no client-identifying names, no client-system-specific code, no secrets. This
test scans the `bookkeeper/` package source for the tokens the AC names and
fails if any leaked in, guarding the line against regression.

Adapters and instance config (which legitimately reference these systems) live
in the private instance repo, never here — so they are never scanned.
"""

from pathlib import Path

import pytest

import bookkeeper

# Client/system/vendor identifiers that must never appear in the framework.
# (Adapters in the private instance repo reference these; the framework cannot.)
_FORBIDDEN = (
    "armature",   # the instance's DB/system
    "gmail",      # the instance's inbox
    "claude",     # model vendor — a deploy/adapter choice
    "anthropic",  # model vendor — a deploy/adapter choice
    "javed",      # client-identifying name
    "receipt",    # instance #1's domain shape — the framework is transaction-generic
)

_PACKAGE_DIR = Path(bookkeeper.__file__).resolve().parent


def _package_sources():
    return sorted(_PACKAGE_DIR.rglob("*.py"))


def test_there_are_sources_to_scan():
    # Guard against a path mistake silently passing the gate.
    assert _package_sources(), f"no package sources found under {_PACKAGE_DIR}"


@pytest.mark.parametrize("path", _package_sources(), ids=lambda p: p.name)
def test_no_client_specific_tokens(path):
    text = path.read_text(encoding="utf-8").lower()
    hits = [token for token in _FORBIDDEN if token in text]
    assert not hits, f"{path.name} leaks client-specific token(s): {hits}"
