"""Public-cleanliness hard gate (charter / issue AC).

This repo is public MIT. The framework must be generic only — no client data,
no client-identifying names, no client-system-specific code, no secrets. This
test scans the public surface for the tokens the AC names and fails if any
leaked in, guarding the line against regression.

Adapters and instance config (which legitimately reference these systems) live
in the private instance repo, never here — so they are never scanned.

Scan root (widened per issue #7, extended to `jr_analyst` per issue #8). Beyond
the framework packages it also covers the public docs and the test suite — the
places a client name or secret is just as likely to slip in as in code:

    README.md  bookkeeper.md  class-template.md  pyproject.toml
    bookkeeper/  jr_analyst/  examples/  tests/

Flat-A: one gate covers both framework packages; the scan root stays the repo
root, so a leak into either package (or the shared docs/tests) is caught here.

Context-awareness — the two named traps *and* the legitimate mentions
------------------------------------------------------------------------
A blunt substring denylist over this wider root would false-positive on genuine
framework text, so the scan is deliberately context-aware:

- **Trap 1 — `HstRegime` is framework, not a client leak.** The framework ships
  `HstRegime` in `skills/track_tax.py` (a config-selected regime, per the
  Canada/HST-only v1 decision), so `hst` / `cra` / `ontario` / `canada` are
  **not** on the denylist. Adding them would flag the framework's own regime.

- **Trap 2 — the guard must not trip on itself.** This file *contains* the
  denylist and its allowances as string literals, so it is excluded from its own
  scan (`_GUARD_FILE`).

- **Documented allowances.** A handful of denylist terms legitimately appear in
  the public surface — as a reference to the `CLAUDE.md` contract file, a
  negative example ("no QBO / OAuth specifics leak in"), a charter example
  ("receipts"), or a migration note recording a *generalized-away* Javed shape
  (`Expense.job_id` → `attribution_target_id`). Each such occurrence is pinned in
  `_ALLOWED` with a reason **and an exact count**. The count is the regression
  wedge: the one documented mention passes, but a *new* occurrence (e.g. a
  reintroduced `job_id` field) pushes the count over the allowance and fails —
  so allowing the mention never blunts the guard against a real re-leak.
"""

from pathlib import Path

import pytest

import bookkeeper

_REPO_ROOT = Path(bookkeeper.__file__).resolve().parent.parent
_GUARD_FILE = Path(__file__).resolve()

# Named public-surface files at the repo root, plus every source file under the
# framework packages and the test suite. Suffixes kept narrow (source + docs +
# config) so a stray binary/cache file is never read.
_ROOT_DOCS = ("README.md", "bookkeeper.md", "class-template.md", "pyproject.toml")
# `examples/` is public too (the README quickstart runs it), so it is scanned
# alongside the framework packages and the test suite. `jr_analyst` joins the
# scan under the same flat-A gate as `bookkeeper` (issue #8); the analyst's own
# tests already fall under `tests/`.
_SCAN_PACKAGES = ("bookkeeper", "jr_analyst", "tests", "examples")
_SCAN_SUFFIXES = (".py", ".md", ".toml")

# Tokens that must never appear in the public framework. Case-insensitive
# substring match. Adapters/instance config in the private repo reference these;
# the framework cannot. Deliberately NOT included: hst / cra / ontario / canada
# — the framework's own `HstRegime` uses those legitimately (trap 1).
_FORBIDDEN = (
    # --- client / instance system + identity names ---
    "armature",     # the instance's DB/system
    "gmail",        # the instance's inbox
    "javed",        # client-identifying name
    "hermes",       # an instance system name
    "stupeters",    # a deploy/identity handle
    "quickbooks",   # a specific accounting vendor (a deploy choice)
    "qbo",          # QuickBooks Online — an accountant-format/adapter choice
    # --- model vendor (a deploy/adapter choice, not the framework's) ---
    "claude",
    "anthropic",
    # --- deploy secrets / infra (adapter concerns, never in the framework) ---
    "postgres",
    "database_url",
    "oauth",
    "dotenv",
    "api_key",
    # --- instance #1's domain shape (the framework is transaction-generic) ---
    "receipt",
    # --- Javed data-model shapes we generalized away (guard against regression) ---
    "job_id",             # → Transaction.attribution_target_id
    "expensekind",        # the dropped ExpenseKind enum
    "material_purchase",
    "production_expense",
)

# Legitimate, documented occurrences: (repo-relative posix path, token) → (count, why).
# `count` = number of lines in that file that contain the token (case-insensitive).
# An exact match is required: a NEW occurrence (count too high) is a leak; a
# REMOVED one (count too low / file gone) is a stale allowance to delete. Either
# way the guard, not a human's memory, stays the source of truth.
_ALLOWED = {
    # References to the CLAUDE.md L0-contract file, not the model vendor.
    ("bookkeeper.md", "claude"): (2, "names the CLAUDE.md contract file, not the model vendor"),
    # The charter cites receipts as one *example* transaction artifact.
    ("bookkeeper.md", "receipt"): (1, "charter names receipts as one example transaction artifact"),
    ("tests/test_orchestrator.py", "receipt"): (1, "notes the suite was ported from instance #1's receipt intake"),
    # QBO named only as a negative example / generic format value — never rendered to.
    ("bookkeeper/contracts.py", "qbo"): (1, "docstring names QBO as one example accountant format"),
    ("bookkeeper/skills/generate_package.py", "qbo"): (1, "docstring states no QBO/format specifics leak into the package"),
    ("tests/test_generate_package.py", "qbo"): (4, "uses 'qbo-export' as a generic example accountant_format value"),
    # OAuth named only to say it stays OUT of the framework (a deploy secret).
    ("bookkeeper/config.py", "oauth"): (1, "docstring explains OAuth secrets stay in the instance repo"),
    ("pyproject.toml", "oauth"): (1, "comment lists OAuth among external deps kept out of the framework"),
    # Migration notes recording the generalized-away Javed shapes.
    ("bookkeeper/model.py", "job_id"): (1, "migration note: Expense.job_id → attribution_target_id"),
    ("bookkeeper/model.py", "expensekind"): (1, "migration note: the dropped ExpenseKind enum"),
}


def _scan_paths() -> list[Path]:
    """Every public-surface file to scan, minus this guard file itself (trap 2)."""
    paths: list[Path] = []
    for name in _ROOT_DOCS:
        p = _REPO_ROOT / name
        if p.exists():
            paths.append(p)
    for package in _SCAN_PACKAGES:
        for p in (_REPO_ROOT / package).rglob("*"):
            if p.suffix in _SCAN_SUFFIXES and "__pycache__" not in p.parts:
                paths.append(p)
    return sorted(
        {p for p in paths if p.resolve() != _GUARD_FILE},
        key=lambda p: p.as_posix(),
    )


def _relpath(path: Path) -> str:
    return path.resolve().relative_to(_REPO_ROOT).as_posix()


def _line_hits(text: str, token: str) -> int:
    """Number of lines in `text` containing `token` (case-insensitive)."""
    return sum(1 for line in text.splitlines() if token in line.lower())


def test_there_are_sources_to_scan():
    # Guard against a path mistake silently passing the gate: the scan must cover
    # both framework packages and the widened root (docs + tests), or it proves
    # nothing. The `jr_analyst` anchor sits in the nested `skills/` dir, so it
    # also proves the rglob reaches into the package, not just its top level.
    scanned = {_relpath(p) for p in _scan_paths()}
    assert scanned, "no sources found to scan"
    for expected in (
        "bookkeeper.md",
        "pyproject.toml",
        "bookkeeper/orchestrator.py",
        "jr_analyst/skills/ingest_and_align.py",
        "examples/quickstart.py",
    ):
        assert expected in scanned, f"widened scan is missing {expected}"
    assert any(p.startswith("tests/") for p in scanned), "widened scan is missing tests/"


@pytest.mark.parametrize("path", _scan_paths(), ids=_relpath)
def test_no_client_specific_tokens(path):
    """Every scanned file must be clean, save the exact documented allowances."""
    rel = _relpath(path)
    text = path.read_text(encoding="utf-8").lower()

    leaks = []
    for token in _FORBIDDEN:
        found = _line_hits(text, token)
        allowed = _ALLOWED.get((rel, token), (0, ""))[0]
        if found != allowed:
            if allowed == 0:
                leaks.append(f"{token!r}: leaks on {found} line(s)")
            else:
                leaks.append(
                    f"{token!r}: {found} line(s), allowance is {allowed} "
                    f"(new occurrence, or stale allowance to update)"
                )
    assert not leaks, f"{rel} public-cleanliness violation(s): {leaks}"


def test_no_stale_allowances():
    """Every allowance must still correspond to a real occurrence.

    Complements the per-file scan: if an allowed occurrence is cleaned up (or its
    file renamed/deleted), the allowance goes dead — and a dead allowance could
    later silently mask a re-leak. Failing here forces it to be pruned.
    """
    stale = []
    for (rel, token), (count, _why) in _ALLOWED.items():
        path = _REPO_ROOT / rel
        if not path.exists():
            stale.append(f"{rel!r} (allowance for {token!r}): file not found")
            continue
        found = _line_hits(path.read_text(encoding="utf-8").lower(), token)
        if found != count:
            stale.append(
                f"{rel!r} {token!r}: allowance {count}, found {found}"
            )
    assert not stale, f"stale/incorrect allowance(s): {stale}"


def test_guard_actually_detects_a_planted_token():
    """The scan is not vacuously green: a planted forbidden token is caught."""
    planted = "an adapter for javed's quickbooks over postgres with an api_key"
    caught = [token for token in _FORBIDDEN if _line_hits(planted, token)]
    assert {"javed", "quickbooks", "postgres", "api_key"} <= set(caught)
