# jr_analyst

The **Junior Analyst** framework — the adapter-agnostic core of a forward-looking FP&A analyst. It reads a period's actuals-to-date, aligns each 1:1 to the budget it belongs to, and grades every figure on a **certainty ladder** for human review. Read-only by construction: it proposes a graded review surface and writes nothing canonical.

- **Canonical spec:** [`CHARTER.md`](CHARTER.md) — the durable (Executor, Standing, FP&A) charter this package implements. The charter is the spec; this package is its reusable implementation.
- **Class map:** the jr-analyst *is* the **FP&A track**; the [Bookkeeper](../bookkeeper.md) *is* the **Accounting track**. Same transaction trunk, two readers.

## Where it lives

jr_analyst ships as a **flat second package inside the `bookkeeper` distribution** — no separate pyproject, distribution, or version; it shares the `bookkeeper` release (there is no `jr_analyst.__version__`), and the root test suite covers both packages. Installing the distribution installs both:

```bash
pip install -e .            # the framework core (pure standard library)
pip install -e ".[test]"    # + pytest / pytest-asyncio to run the suite
```

It ships a PEP 561 `py.typed` marker, so its types are visible to a consumer's type checker under the same install story as the Bookkeeper framework — see the [repo README](../README.md#install) for the editable-install / mypy caveat.

## The shape

An **agnostic core driven through read-only ports**, mirroring the Bookkeeper's ports/adapters line. Every external system — the Bookkeeper pipeline's output, a budget store — is an **adapter** implemented against a port, and **adapters plus per-instance config live in the private instance repo, never here.**

| Piece | What it is |
|---|---|
| [`model.py`](model.py) | The frozen data model: the `Certainty` ladder, `ActualLine` / `BudgetLine`, the 1:1 `AlignedPair`, the escalated `UnmappedLine`, and the `AlignedDataset` the skill returns. `Decimal` money throughout; every row keeps its `source_ref`. |
| [`ports.py`](ports.py) | The two read ports — `ActualsSource` and `BudgetSource`. **There is no sink port:** the analyst has no seam through which to write. That is the charter §5 boundary made structural. |
| [`certainty.py`](certainty.py) | `derive_certainty(period, prior_period_state)` — the pure closed-vs-open grading rule an adapter stamps each actual with, with a distinct `CANNOT_ORDER` fail-safe. |
| [`config.py`](config.py) | `AnalystConfig` — the typed, frozen per-instance surface, fail-fast validated (`from_mapping` reports every missing required field at once). |
| [`skills/`](skills/) | One module per charter skill. Slice 1: [`ingest_and_align`](skills/ingest_and_align.py). Slice 2: [`flag_variance`](skills/flag_variance.py). |

## Status

Charter v0.1 · slices 1–2 built.

- **`ingest_and_align`** — **built** (slice 1). Aligns certainty-tagged realized actuals to the budget 1:1 and escalates the rest.
- **`flag_variance`** — **built** (slice 2). Reads the aligned pairs and surfaces each material actual-vs-budget variance — a signed exact-`Decimal` delta, classified over-/under-budget, above the `variance_floor`. A pure, **sync** reader (it drives no port): it proposes a graded, traceable flag and writes nothing.
- **`build_report` / `explain_variance`** — **planned** (slices 3–4), along with the forward-looking `committed` / `anticipated` ladder rungs as input extensions.

## Illustrative usage

A deployment implements the ports as adapters over its own systems and binds the config in its **private instance repo**. The sketch below fakes the two read ports with in-memory lists to show the shape — it is illustrative, not a CI-pinned example (the runnable, suite-pinned quickstart today is the Bookkeeper's, in the [repo README](../README.md#a-minimal-runnable-example)).

```python
import asyncio
from decimal import Decimal

from jr_analyst import (
    ActualLine, ActualsSource, AnalystConfig, BudgetLine, BudgetSource,
    Certainty, ingest_and_align,
)


class ListActuals(ActualsSource):
    """A tiny read adapter: serves a fixed list of already-graded actuals."""
    def __init__(self, lines): self._lines = list(lines)
    async def fetch_realized(self, window): return list(self._lines)


class ListBudget(BudgetSource):
    def __init__(self, lines): self._lines = list(lines)
    async def fetch_budget(self, period): return list(self._lines)


async def run():
    actuals = ListActuals([
        # a realized-open cost on the live period, attributed to a job
        ActualLine("materials", "job-1", "2026-Q2", Decimal("1200"),
                   "actual-1", Certainty.REALIZED_OPEN),
        # spend the plan did not anticipate — will escalate as unmatched_actual
        ActualLine("travel", "job-1", "2026-Q2", Decimal("300"),
                   "actual-2", Certainty.REALIZED_OPEN),
    ])
    budget = ListBudget([
        BudgetLine("materials", "job-1", "2026-Q2", Decimal("1000"), "budget-1"),
    ])
    # `budget_source_ref` is the one required field; align_on defaults to (account, period).
    config = AnalystConfig.from_mapping({"budget_source_ref": "in-memory"})
    return await ingest_and_align(actuals, budget, config, "2026-Q2")


dataset = asyncio.run(run())
for pair in dataset.aligned:
    print(f"aligned  {pair.actual.account:<10} {pair.actual.amount} vs "
          f"{pair.budget.amount}  [{pair.certainty.value}]")
for u in dataset.unmapped:
    print(f"unmapped {u.line.account:<10} → {u.kind.value}")
# aligned  materials  1200 vs 1000  [realized_open]
# unmapped travel     → unmatched_actual
```

The skill's only source-touching arguments are the read-side ports — there is no writer in its signature, so it *cannot* publish (charter §5). It returns the `AlignedDataset`; resolving any escalation is a later, human-gated step.

---

Adapters and per-instance config live in the **private instance repo, never here** — this package is generic only.
