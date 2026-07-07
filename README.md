# agent-classes

The Bessemer vertical agent-class library: reliable, bounded-autonomy AI agents that fill real roles in small organizations.

Each class is anchored by a **charter** — a durable specification a deployment runs against — and, once it has matured into a working product, a **shared framework package** the deployment imports and configures. The charter is the canonical spec; the framework is its reusable implementation. Adapters and per-instance config live in the private client repo, never here. A class is a (position, lifecycle, domain) binding from the [agent-role topology](https://github.com/usebessemer/research/blob/main/theory/agent-role-topology.md): typed by the topology, scaffolded by [icm-kit](https://github.com/usebessemer/icm-kit). The classes are the third layer of the Bessemer stack: **research** (the methodology) → **tooling** (the factory) → **classes** (the product).

## The classes

| Class | Position × Lifecycle | Vertical | Status |
|---|---|---|---|
| [Bookkeeper](bookkeeper.md) | Executor × Standing | vertical-agnostic (any small business) | charter v0.1 · framework **v0.1.0 shipped** |
| [DevLead](devlead.md) | Lead × Standing | cross-vertical (the management character) | charter v0.1 |
| Legal Admin | Executor × Standing | small law firms | planned |

The library carries both kinds: **vertical-agnostic** classes (the Bookkeeper's books-and-tax shape fits any small business) and **vertical-specific** ones (a Legal Admin's obligations are particular to legal practice).

## The class/instance seam

A class charter is **public and reusable**: it names per-instance *fields* but holds no values. A deployment binds those fields to a specific organization, its chart of accounts, its jurisdiction, its systems of record, and that instance config plus any client data lives in the **private client repo, never here**. The charter and the framework package are the reusable IP; the instance — its adapters, config, and client data — is bespoke and private.

## Quickstart — using the Bookkeeper framework

The [Bookkeeper](bookkeeper.md) charter has matured into a working framework package (`bookkeeper/`) you can install and run today. It is a **headless, dependency-free core**: the charter §3 config (`BookkeeperConfig`), the skill **ports** that adapters implement, and the §4 **skills** that compute over what the pipeline stored — all under the §5 **propose / never-publish** boundary (a skill *returns* a proposal; publishing is a separate, human-gated step). The core is pure standard library; every external system — a database, an inbox, a document store — is an **adapter** you implement against a port. **Adapters and per-instance config live in the private instance repo, never here** — the framework is generic only.

### Install

```bash
pip install -e .            # the framework core (pure standard library)
pip install -e ".[test]"    # + pytest / pytest-asyncio to run the suite
```

### A minimal runnable example

Implement one port (a list-backed `LedgerSource`), build a `BookkeeperConfig` with a small chart of accounts, and run the `categorize` skill for a period. The skill reads the period and **proposes** a chart account per transaction — an explicit owner rule pre-fills at full confidence, a chart match at a scaled confidence, and anything it can't place confidently is **flagged for a human, never given a fabricated account**. It writes nothing: its only ledger-touching argument is the read-side `LedgerSource`, so it *cannot* publish (charter §5.4).

This is [`examples/quickstart.py`](examples/quickstart.py), run green by the suite so it can't drift:

```python
"""Runnable quickstart for the Bookkeeper framework (embedded in the README, CI-tested).

A minimal, dependency-free walk through the ports/adapters shape:

  1. implement a port  — a ~5-line `LedgerSource` backed by a Python list,
  2. build a config    — a `BookkeeperConfig` (the charter §3 fields, incl. a
     small chart of accounts and the §5 categorize boundary),
  3. run a skill       — `categorize`, which reads the period and *proposes* a
     chart account per transaction, and
  4. read the report   — proposals + flags. The skill writes nothing: its only
     ledger-touching argument is the read-side `LedgerSource`; there is no sink
     in its signature, so it *cannot* publish (charter §5.4).

A real deployment implements the ports as adapters over its own systems (a
database, an inbox, a document store) and binds §3 to its organization — all in
the private instance repo, never in this public framework. This file fakes the
one port it needs with an in-memory list so the quickstart runs with no external
system.

Run it:  python examples/quickstart.py
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from decimal import Decimal

from bookkeeper import BookkeeperConfig, LedgerSource, Transaction, categorize


class ListLedger(LedgerSource):
    """A tiny `LedgerSource` adapter: serves a fixed list of transactions.

    Stands in for a real adapter (which would read the instance's books and live
    in the private instance repo). It ignores `period` and returns the same list.
    """

    def __init__(self, transactions: list[Transaction]):
        self._transactions = list(transactions)

    async def fetch_for_period(self, period: str) -> list[Transaction]:
        return list(self._transactions)


def _txn(vendor: str, description: str) -> Transaction:
    """A stored transaction, already attributed and ready to read back.

    Amounts/tax/date are illustrative — `categorize` proposes an account from the
    vendor + description and does no money math (that is `track_tax`'s job).
    """
    return Transaction(
        attribution_target_id="project-alpha",
        vendor=vendor,
        amount=Decimal("0"),
        tax=Decimal("0"),
        date=datetime(2026, 4, 15),
        description=description,
        artifact_bytes=b"",
    )


def build_config() -> BookkeeperConfig:
    """The charter §3 config — a small chart + the §5 categorize boundary set live."""
    return BookkeeperConfig.from_mapping(
        {
            "chart_of_accounts": ("Office Supplies", "Travel and Meals", "Construction Materials"),
            "accounting_method": "cash",
            "jurisdiction": "CA-ON",
            "tax_regime": "HST",
            "accountant_format": "generic-export",
            "attribution_targets": ("project-alpha",),
            "books_location": "in-memory-list",
            "intake_channel": "manual",
            # The §5 boundary. Set → live (confident matches pre-filled as
            # proposals); unset → inert (every transaction surfaced for review).
            "confidence_thresholds": {"categorize": 0.5},
            # An explicit owner vendor→account rule (namespaced `category:`).
            "owner_policies": {"category:home depot": "Construction Materials"},
        }
    )


async def run() -> object:
    """Build the adapter + config, run `categorize`, and return the report."""
    ledger = ListLedger(
        [
            _txn("Home Depot", "lumber and fasteners"),
            _txn("Staples", "office supplies order"),
            _txn("Corner Cafe", "flat white"),
        ]
    )
    return await categorize(ledger, build_config(), "2026-Q2")


def _print_report(report: object) -> None:
    print(f"categorize — {report.period}  (proposed for sign-off; writes nothing)")
    for proposal in report.proposals:
        print(
            f"  proposal  {proposal.transaction.vendor:<12}"
            f" → {proposal.proposed_account:<22}"
            f"  [{proposal.source}, {proposal.confidence:.2f}]"
        )
    for flag in report.flagged:
        print(f"  flagged   {flag.transaction.vendor:<12} → surfaced for human review")


def main() -> None:
    _print_report(asyncio.run(run()))


if __name__ == "__main__":
    main()
```

Run it:

```bash
python examples/quickstart.py
```

which prints the proposed categorization — two confident proposals and one flagged transaction, computed and returned, written nowhere:

```
categorize — 2026-Q2  (proposed for sign-off; writes nothing)
  proposal  Home Depot   → Construction Materials  [owner-rule, 1.00]
  proposal  Staples      → Office Supplies         [chart-match, 0.90]
  flagged   Corner Cafe  → surfaced for human review
```

To go live, a deployment implements the other ports as adapters over its own systems, binds the §3 fields to its organization, and configures the §5 thresholds — all in its **private instance repo**. Leaving `confidence_thresholds["categorize"]` unset keeps the boundary **inert**: every transaction is surfaced for review and nothing is pre-filled, so no instance goes live silently.

### Run it as an app

The framework is the headless core. A runnable **local app** — a UI over a local store, the interaction surface a non-technical owner actually uses — is being built as a separate open-source repo; it will import this framework and provide the "run it as an app" path. *Forthcoming.*

## Adding a class

Start from [`class-template.md`](class-template.md), the shape every class follows, derived from the topology's concrete-binding form. The spine of every executor class is its **autonomy / review boundary**: where the agent acts unattended versus where it escalates to the human, anchored to where errors carry real consequence, with a fail-safe-never-silent floor.

## License

MIT.
