"""The README quickstart is real and stays real (issue #24 AC).

Two guarantees, so the public example can't rot:

- **It runs.** Importing and running `examples/quickstart.py` produces the
  expected `CategorizationReport` — the ports → skill → report path actually
  works (the owner rule and the chart match propose; the unmatched vendor is
  flagged), and the skill writes nothing (its only ledger argument is a
  read-side port, so there is nothing it *could* write).
- **The README shows exactly it.** The `examples/quickstart.py` source is
  embedded verbatim in `README.md`, so the shown example can never drift from
  the tested file — changing one without the other fails here. This is the AC's
  "keep the example from rotting" guarantee: a prose-only snippet that drifts is
  worse than none.
"""

from pathlib import Path

import bookkeeper
from bookkeeper import CategorizationReport
from examples.quickstart import run

_REPO_ROOT = Path(bookkeeper.__file__).resolve().parent.parent
_EXAMPLE = _REPO_ROOT / "examples" / "quickstart.py"
_README = _REPO_ROOT / "README.md"


async def test_quickstart_runs_and_returns_expected_report():
    """The example's ports → skill → report path runs and returns the proposals."""
    report = await run()

    assert isinstance(report, CategorizationReport)
    assert report.period == "2026-Q2"

    # Owner rule → Construction Materials (full confidence); chart match →
    # Office Supplies (scaled). Both are confident proposals.
    proposed = {p.transaction.vendor: p.proposed_account for p in report.proposals}
    assert proposed == {
        "Home Depot": "Construction Materials",
        "Staples": "Office Supplies",
    }
    # The unmatched vendor is surfaced for a human, never given a fabricated
    # account (§5: uncertain → review, never invent a category).
    assert [f.transaction.vendor for f in report.flagged] == ["Corner Cafe"]


def test_readme_embeds_the_example_verbatim():
    """The README's quickstart block is the exact `examples/quickstart.py` source.

    Pins the two public copies together: the tested file is the single source of
    truth, and the README shows precisely it. Edit the example without updating
    the README (or vice versa) and this fails — the anti-rot guarantee.
    """
    example_src = _EXAMPLE.read_text(encoding="utf-8").strip()
    readme = _README.read_text(encoding="utf-8")
    assert example_src in readme, (
        "README.md must embed examples/quickstart.py verbatim so the shown "
        "example can't drift from the CI-tested file"
    )
