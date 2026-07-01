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
