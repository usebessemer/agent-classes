"""`trackTax` skill tests — the §4 tax computation under the §5 boundary.

Each test pins one bullet of the issue's acceptance criteria:

- per-target + period HST totals over a fake `LedgerSource`, in `Decimal`
- NULL / absent tax → 0
- totals are exact `Decimal` (not float), proven on a case float gets wrong
- `TaxRegime` seam: only HST registered; an unknown `tax_regime` fails fast
- §5.4: `track_tax` writes nothing canonical (only reads) — proposed, not published
- §5.3: tax with no resolvable target is flagged, not silently totalled
- HST totals match the reference's semantics (sum of captured tax per target,
  refunds signed negative reduce the total) on a representative fixture
"""

from decimal import Decimal

import pytest

from bookkeeper.skills.track_tax import (
    HstRegime,
    TaxSummary,
    UnknownTaxRegime,
    select_regime,
    track_tax,
)
from tests.fakes import FakeLedger, FakeLedgerSource, make_config, make_transaction


def _hst_config(**overrides):
    """A config whose tax regime is HST (the v1-registered regime)."""
    overrides.setdefault("tax_regime", "HST")
    return make_config(**overrides)


# --- per-target + period totals, in Decimal --------------------------------


async def test_per_target_and_period_totals_in_decimal():
    """Reclaimable HST totals per attribution target, plus the period total."""
    source = FakeLedgerSource(
        by_period={
            "2026-Q2": [
                make_transaction(attribution_target_id="target-a", tax=10.00),
                make_transaction(attribution_target_id="target-a", tax=5.25),
                make_transaction(attribution_target_id="target-b", tax=2.50),
            ]
        }
    )
    summary = await track_tax(source, _hst_config(), "2026-Q2")

    assert isinstance(summary, TaxSummary)
    assert summary.period == "2026-Q2"
    assert summary.regime == "HST"
    assert source.fetched == ["2026-Q2"]  # read exactly the period asked for

    by_target = {t.attribution_target_id: t for t in summary.per_target}
    assert by_target["target-a"].reclaimable == Decimal("15.25")
    assert by_target["target-a"].transaction_count == 2
    assert by_target["target-b"].reclaimable == Decimal("2.50")
    assert summary.period_total == Decimal("17.75")

    # Every figure is Decimal, never float.
    assert isinstance(summary.period_total, Decimal)
    assert all(isinstance(t.reclaimable, Decimal) for t in summary.per_target)


async def test_per_target_ordering_is_deterministic():
    """Targets come back in a stable (sorted) order, regardless of input order."""
    source = FakeLedgerSource(
        by_period={
            "2026-Q2": [
                make_transaction(attribution_target_id="target-c", tax=1.00),
                make_transaction(attribution_target_id="target-a", tax=1.00),
                make_transaction(attribution_target_id="target-b", tax=1.00),
            ]
        }
    )
    summary = await track_tax(source, _hst_config(), "2026-Q2")
    assert [t.attribution_target_id for t in summary.per_target] == [
        "target-a",
        "target-b",
        "target-c",
    ]


# --- NULL / absent tax → 0 --------------------------------------------------


async def test_absent_tax_counts_as_zero():
    """Transactions with absent (None) or zero tax contribute 0, not an error."""
    source = FakeLedgerSource(
        by_period={
            "2026-Q2": [
                make_transaction(attribution_target_id="target-a", tax=12.00),
                make_transaction(attribution_target_id="target-a", tax=0.0),
                make_transaction(attribution_target_id="target-a", tax=None),  # NULL
            ]
        }
    )
    summary = await track_tax(source, _hst_config(), "2026-Q2")

    target_a = summary.per_target[0]
    assert target_a.attribution_target_id == "target-a"
    assert target_a.reclaimable == Decimal("12.00")
    # The zero / NULL rows are still traceable members of the target, just 0 each.
    assert target_a.transaction_count == 3
    assert summary.period_total == Decimal("12.00")


async def test_target_with_only_untaxed_transactions_reports_zero():
    """A target whose transactions all carry no tax reports 0 (not absent)."""
    source = FakeLedgerSource(
        by_period={
            "2026-Q2": [
                make_transaction(attribution_target_id="target-z", tax=0.0),
                make_transaction(attribution_target_id="target-z", tax=None),
            ]
        }
    )
    summary = await track_tax(source, _hst_config(), "2026-Q2")
    assert summary.per_target[0].reclaimable == Decimal("0")
    assert summary.period_total == Decimal("0")


# --- Decimal, not float -----------------------------------------------------


async def test_totals_are_exact_decimal_not_float():
    """0.10 + 0.20 totals to an exact 0.30 — float would give 0.30000000000000004."""
    source = FakeLedgerSource(
        by_period={
            "2026-Q2": [
                make_transaction(attribution_target_id="target-a", tax=0.10),
                make_transaction(attribution_target_id="target-a", tax=0.20),
            ]
        }
    )
    summary = await track_tax(source, _hst_config(), "2026-Q2")

    assert summary.per_target[0].reclaimable == Decimal("0.30")
    assert summary.period_total == Decimal("0.30")
    # The float sum is *not* 0.30 — proving the skill did not total in float.
    assert (0.10 + 0.20) != 0.30
    assert summary.period_total != Decimal(0.10 + 0.20)


# --- regime seam: HST only, unknown fails fast ------------------------------


async def test_hst_regime_selected_case_insensitively():
    """`config.tax_regime` selects HST; matching is case-insensitive."""
    assert isinstance(select_regime(_hst_config(tax_regime="HST")), HstRegime)
    assert isinstance(select_regime(_hst_config(tax_regime="hst")), HstRegime)
    assert select_regime(_hst_config()).name == "HST"


async def test_unknown_regime_fails_fast():
    """An unregistered tax_regime raises (never silently totals nothing)."""
    source = FakeLedgerSource(
        by_period={"2026-Q2": [make_transaction(tax=10.0)]}
    )
    for unknown in ("VAT", "US-SALES", "standard"):
        with pytest.raises(UnknownTaxRegime):
            await track_tax(source, _hst_config(tax_regime=unknown), "2026-Q2")


async def test_unknown_regime_fails_before_reading():
    """Fail-fast happens before any ledger read — a clear error, not a partial run."""
    source = FakeLedgerSource(by_period={"2026-Q2": [make_transaction(tax=10.0)]})
    with pytest.raises(UnknownTaxRegime):
        await track_tax(source, _hst_config(tax_regime="VAT"), "2026-Q2")
    assert source.fetched == []  # never reached the read


# --- §5.4: writes nothing canonical -----------------------------------------


async def test_writes_nothing_canonical():
    """§5.4: track_tax only reads; it stores nothing (proposed, never published).

    Uses the combined read+write fake (one store, both ports) so the proof is
    concrete: after a full run, no `store` call was made.
    """
    ledger = FakeLedger(
        by_period={
            "2026-Q2": [
                make_transaction(attribution_target_id="target-a", tax=10.00),
                make_transaction(attribution_target_id="target-b", tax=5.00),
            ]
        }
    )
    summary = await track_tax(ledger, _hst_config(), "2026-Q2")

    assert summary.period_total == Decimal("15.00")  # it did compute
    assert ledger.store_calls == []  # ...but wrote nothing canonical
    assert ledger.fetched == ["2026-Q2"]  # only read


# --- §5.3: ambiguous tax flagged, not silently totalled ---------------------


async def test_tax_without_target_is_flagged_not_totalled():
    """§5.3: captured tax with no resolvable target → flagged, kept out of totals."""
    source = FakeLedgerSource(
        by_period={
            "2026-Q2": [
                make_transaction(attribution_target_id="target-a", tax=10.00),
                make_transaction(attribution_target_id="", tax=4.00),  # tax, no target
                make_transaction(attribution_target_id="   ", tax=1.00),  # blank target
            ]
        }
    )
    summary = await track_tax(source, _hst_config(), "2026-Q2")

    # Only the resolvable target is totalled; the period total excludes the flagged.
    assert [t.attribution_target_id for t in summary.per_target] == ["target-a"]
    assert summary.period_total == Decimal("10.00")

    # Both untargeted-but-taxed transactions are surfaced for review.
    assert len(summary.flagged) == 2
    assert all(f.reason for f in summary.flagged)  # each carries a §5.3 reason


async def test_untaxed_untargeted_transaction_is_neither_totalled_nor_flagged():
    """No tax and no target → nothing to reclaim, nothing to review (silently skipped)."""
    source = FakeLedgerSource(
        by_period={
            "2026-Q2": [
                make_transaction(attribution_target_id="target-a", tax=7.00),
                make_transaction(attribution_target_id="", tax=0.0),  # nothing to do
            ]
        }
    )
    summary = await track_tax(source, _hst_config(), "2026-Q2")
    assert summary.period_total == Decimal("7.00")
    assert summary.flagged == ()
    assert len(summary.per_target) == 1


# --- matches the reference's HST semantics ----------------------------------


async def test_matches_reference_semantics_sum_per_target():
    """HST totals = sum of captured tax per target, NULL→0, refunds reduce the total.

    A representative fixture mirroring instance #1's per-project HST report: a
    target accrues reclaimable HST across many transactions; a refund (signed
    negative) reduces it; a NULL-tax historical row contributes 0.
    """
    source = FakeLedgerSource(
        by_period={
            "2026-Q2": [
                # target-a: supplies + production costs, one refund, one NULL row.
                make_transaction(attribution_target_id="target-a", vendor="Supplier 1", tax=13.00),
                make_transaction(attribution_target_id="target-a", vendor="Supplier 2", tax=7.80),
                make_transaction(attribution_target_id="target-a", vendor="Refund", tax=-2.60),
                make_transaction(attribution_target_id="target-a", vendor="Historical", tax=None),
                # target-b: a single taxed expense.
                make_transaction(attribution_target_id="target-b", vendor="Catering", tax=9.10),
            ]
        }
    )
    summary = await track_tax(source, _hst_config(), "2026-Q2")

    by_target = {t.attribution_target_id: t.reclaimable for t in summary.per_target}
    # 13.00 + 7.80 - 2.60 + 0 = 18.20
    assert by_target["target-a"] == Decimal("18.20")
    assert by_target["target-b"] == Decimal("9.10")
    assert summary.period_total == Decimal("27.30")


async def test_refund_negative_tax_reduces_target_total():
    """A refund (negative tax) reduces the target's reclaimable, carrying its sign."""
    source = FakeLedgerSource(
        by_period={
            "2026-Q2": [
                make_transaction(attribution_target_id="target-a", tax=22.19),
                make_transaction(attribution_target_id="target-a", tax=-22.19),  # full refund
            ]
        }
    )
    summary = await track_tax(source, _hst_config(), "2026-Q2")
    assert summary.per_target[0].reclaimable == Decimal("0.00")
    assert summary.period_total == Decimal("0")


# --- empty period -----------------------------------------------------------


async def test_empty_period_returns_zero_summary():
    """A period with no transactions returns an empty, zero, un-flagged summary."""
    summary = await track_tax(FakeLedgerSource(), _hst_config(), "2026-Q1")
    assert summary.per_target == ()
    assert summary.flagged == ()
    assert summary.period_total == Decimal("0")
    assert summary.regime == "HST"
    assert summary.period == "2026-Q1"
