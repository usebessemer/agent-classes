"""`flagAnomaly` skill tests — advisory mechanical-anomaly detection under §5.

Each test pins one bullet of the issue's acceptance criteria:

- `flag_anomaly` reads via `LedgerSource`, returns an advisory `AnomalyReport` of
  the three flag kinds, and **writes nothing canonical / never mutates / never
  blocks** (mutation-proven with the combined read+write ledger fake)
- the three mechanical checks each detected: duplicates (vendor + amount +
  near-date), over-materiality (`abs(amount) > materiality_floor`, §5.6), malformed
  (missing vendor / date, zero / absent amount)
- **§2 line held**: a clean-but-"trending" period produces no flags (no trend /
  forecast / pattern / predictive), and the docstring documents the scope
- `materiality_floor` is `Decimal`, and the over-materiality check is **inert when
  unset** (duplicates + malformed still scan)
- deterministic ordering; in-memory fakes only; the public surface is exported

Money is `Decimal` at the model (exact currency); these fixtures pass `Decimal`
amounts so a difference / threshold compare is exact, never float rounding.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from bookkeeper.model import Transaction
from bookkeeper.skills.flag_anomaly import (
    AnomalyFlag,
    AnomalyKind,
    AnomalyReport,
    flag_anomaly,
)
from tests.fakes import (
    FakeLedger,
    FakeLedgerSource,
    make_config,
    make_transaction,
)

# A fixed base date so the date-window tests are deterministic (no wall clock).
_BASE = datetime(2026, 5, 15, 12, 0, 0, tzinfo=timezone.utc)


def _day(offset: int) -> datetime:
    """The base date shifted by `offset` calendar days."""
    return _BASE + timedelta(days=offset)


def _ledger(transactions, period="2026-Q2"):
    """A `FakeLedgerSource` seeded with `transactions` for one period."""
    return FakeLedgerSource(by_period={period: list(transactions)})


def _raw(
    *,
    attribution_target_id="target-001",
    vendor="Acme Supplies",
    amount=Decimal("45.99"),
    tax=Decimal("0"),
    date=_BASE,
    description="",
    artifact_bytes=b"",
) -> Transaction:
    """Construct a `Transaction` directly so a test can inject None date / amount.

    `make_transaction` coalesces `date=None` to a default, so the malformed
    missing-date / absent-amount edge cases need a raw constructor.
    """
    return Transaction(
        attribution_target_id=attribution_target_id,
        vendor=vendor,
        amount=amount,
        tax=tax,
        date=date,
        description=description,
        artifact_bytes=artifact_bytes,
    )


def _kinds(report: AnomalyReport) -> list[AnomalyKind]:
    return [f.kind for f in report.flags]


# --- the public surface ------------------------------------------------------


def test_flag_anomaly_surface_is_exported_from_package():
    """The public surface re-exports through `bookkeeper` (the convention)."""
    import bookkeeper

    for name in ("flag_anomaly", "AnomalyReport", "AnomalyFlag", "AnomalyKind"):
        assert hasattr(bookkeeper, name), f"{name} not exported from bookkeeper"
        assert name in bookkeeper.__all__, f"{name} missing from __all__"
    assert bookkeeper.flag_anomaly is flag_anomaly


async def test_empty_period_returns_empty_report():
    """A period with no transactions flags nothing."""
    report = await flag_anomaly(FakeLedgerSource(), make_config(), "2026-Q1")
    assert isinstance(report, AnomalyReport)
    assert report.period == "2026-Q1"
    assert report.flags == ()


# --- check 1: duplicates -----------------------------------------------------


async def test_duplicate_same_vendor_amount_date_flags_the_group():
    """Two records with the same vendor + amount + date → one DUPLICATE flag, both inside."""
    txns = [
        make_transaction(vendor="Acme Supplies", amount=Decimal("45.99"), date=_day(0)),
        make_transaction(vendor="Acme Supplies", amount=Decimal("45.99"), date=_day(0)),
    ]
    report = await flag_anomaly(_ledger(txns), make_config(), "2026-Q2")

    assert _kinds(report) == [AnomalyKind.DUPLICATE]
    flag = report.flags[0]
    assert isinstance(flag, AnomalyFlag)
    assert len(flag.transactions) == 2  # both/all of the group travel together
    assert flag.reason


async def test_three_duplicates_all_in_one_group():
    """Three identical records → a single DUPLICATE flag holding all three."""
    txns = [
        make_transaction(vendor="Acme", amount=Decimal("10.00"), date=_day(0))
        for _ in range(3)
    ]
    report = await flag_anomaly(_ledger(txns), make_config(), "2026-Q2")
    assert _kinds(report) == [AnomalyKind.DUPLICATE]
    assert len(report.flags[0].transactions) == 3


async def test_duplicate_within_small_window_still_flags():
    """Same vendor + amount one day apart (capture-timing lag) is still a duplicate."""
    txns = [
        make_transaction(vendor="Acme", amount=Decimal("20.00"), date=_day(0)),
        make_transaction(vendor="Acme", amount=Decimal("20.00"), date=_day(1)),
    ]
    report = await flag_anomaly(_ledger(txns), make_config(), "2026-Q2")
    assert _kinds(report) == [AnomalyKind.DUPLICATE]


async def test_same_vendor_amount_far_apart_is_not_a_duplicate():
    """Same vendor + amount a week apart is a recurring charge, not a double-capture.

    The skill never *infers* a recurrence cadence — it simply declines to pair
    records beyond the small window. So nothing is flagged (still mechanical).
    """
    txns = [
        make_transaction(vendor="Acme", amount=Decimal("20.00"), date=_day(0)),
        make_transaction(vendor="Acme", amount=Decimal("20.00"), date=_day(7)),
    ]
    report = await flag_anomaly(_ledger(txns), make_config(), "2026-Q2")
    assert report.flags == ()


async def test_same_vendor_different_amount_is_not_a_duplicate():
    """Same vendor, different amounts → two distinct charges, not a duplicate."""
    txns = [
        make_transaction(vendor="Acme", amount=Decimal("20.00"), date=_day(0)),
        make_transaction(vendor="Acme", amount=Decimal("21.00"), date=_day(0)),
    ]
    report = await flag_anomaly(_ledger(txns), make_config(), "2026-Q2")
    assert all(f.kind != AnomalyKind.DUPLICATE for f in report.flags)


async def test_vendor_normalization_casefold_and_whitespace_groups_duplicates():
    """`"Acme  Supplies"` and `"acme supplies"` key as the same vendor → a duplicate."""
    txns = [
        make_transaction(vendor="Acme  Supplies", amount=Decimal("12.00"), date=_day(0)),
        make_transaction(vendor="acme supplies", amount=Decimal("12.00"), date=_day(0)),
    ]
    report = await flag_anomaly(_ledger(txns), make_config(), "2026-Q2")
    assert _kinds(report) == [AnomalyKind.DUPLICATE]


# --- check 2: over-materiality (§5.6) ---------------------------------------


async def test_over_materiality_flags_large_item_even_when_confidently_attributed():
    """§5.6: a large item is surfaced on size alone, regardless of attribution confidence."""
    txns = [
        make_transaction(
            vendor="BigCo",
            amount=Decimal("5000.00"),  # > the 1000 floor
            date=_day(0),
            attribution_target_id="target-001",  # confidently attributed — still flagged
        )
    ]
    report = await flag_anomaly(_ledger(txns), make_config(), "2026-Q2")
    assert _kinds(report) == [AnomalyKind.OVER_MATERIALITY]
    assert report.flags[0].transactions[0].vendor == "BigCo"


async def test_over_materiality_uses_absolute_value_for_negatives():
    """A large refund (negative amount) clears the floor on `abs` and is flagged."""
    txns = [make_transaction(vendor="BigCo", amount=Decimal("-5000.00"), date=_day(0))]
    report = await flag_anomaly(_ledger(txns), make_config(), "2026-Q2")
    assert _kinds(report) == [AnomalyKind.OVER_MATERIALITY]


async def test_amount_at_the_floor_is_not_flagged_strict_greater_than():
    """The threshold is strict `>`: an amount exactly at the floor is not flagged."""
    txns = [make_transaction(vendor="Edge", amount=Decimal("1000.00"), date=_day(0))]
    report = await flag_anomaly(_ledger(txns), make_config(), "2026-Q2")
    assert report.flags == ()


async def test_over_materiality_inert_when_floor_unset_but_others_still_scan():
    """Inert: an unset `materiality_floor` skips over-materiality; dupes + malformed still run."""
    txns = [
        # a huge item that WOULD be over-materiality if a floor were configured
        make_transaction(vendor="BigCo", amount=Decimal("9999.00"), date=_day(0)),
        # a duplicate pair (needs no threshold)
        make_transaction(vendor="Acme", amount=Decimal("12.00"), date=_day(0)),
        make_transaction(vendor="Acme", amount=Decimal("12.00"), date=_day(0)),
        # a malformed record (needs no threshold)
        make_transaction(vendor="", amount=Decimal("3.00"), date=_day(0)),
    ]
    inert = await flag_anomaly(_ledger(txns), make_config(materiality_floor=None), "2026-Q2")
    kinds = _kinds(inert)
    assert AnomalyKind.OVER_MATERIALITY not in kinds  # inert — skipped
    assert AnomalyKind.DUPLICATE in kinds  # but these still scan
    assert AnomalyKind.MALFORMED in kinds

    # The control: with the floor configured, the huge item is surfaced.
    live = await flag_anomaly(_ledger(txns), make_config(), "2026-Q2")
    assert AnomalyKind.OVER_MATERIALITY in _kinds(live)


# --- check 3: malformed / incomplete ----------------------------------------


async def test_malformed_missing_vendor():
    """A blank vendor is a structural defect → MALFORMED."""
    txns = [make_transaction(vendor="   ", amount=Decimal("5.00"), date=_day(0))]
    report = await flag_anomaly(_ledger(txns), make_config(), "2026-Q2")
    assert _kinds(report) == [AnomalyKind.MALFORMED]
    assert "missing vendor" in report.flags[0].reason


async def test_malformed_missing_date():
    """A None date is a structural defect → MALFORMED."""
    txns = [_raw(vendor="Acme", amount=Decimal("5.00"), date=None)]
    report = await flag_anomaly(_ledger(txns), make_config(), "2026-Q2")
    assert _kinds(report) == [AnomalyKind.MALFORMED]
    assert "missing date" in report.flags[0].reason


async def test_malformed_zero_amount():
    """A zero amount (no magnitude where one is expected) → MALFORMED."""
    txns = [make_transaction(vendor="Acme", amount=Decimal("0"), date=_day(0))]
    report = await flag_anomaly(_ledger(txns), make_config(), "2026-Q2")
    assert _kinds(report) == [AnomalyKind.MALFORMED]
    assert "zero amount" in report.flags[0].reason


async def test_malformed_absent_amount_does_not_crash_other_checks():
    """A None amount is MALFORMED and is skipped by the duplicate / over-materiality keys."""
    txns = [_raw(vendor="Acme", amount=None, date=_day(0))]
    report = await flag_anomaly(_ledger(txns), make_config(), "2026-Q2")
    assert _kinds(report) == [AnomalyKind.MALFORMED]
    assert "absent amount" in report.flags[0].reason


async def test_malformed_multiple_defects_one_flag_lists_them_all():
    """A record with several defects yields one MALFORMED flag enumerating each."""
    txns = [make_transaction(vendor="", amount=Decimal("0"), date=_day(0))]
    report = await flag_anomaly(_ledger(txns), make_config(), "2026-Q2")
    assert _kinds(report) == [AnomalyKind.MALFORMED]
    reason = report.flags[0].reason
    assert "missing vendor" in reason and "zero amount" in reason


async def test_empty_artifact_bytes_is_not_malformed():
    """The read-path projection (`artifact_bytes = b""`) is NOT treated as a defect.

    `LedgerSource` legitimately omits the source blob on the read path, so an empty
    `artifact_bytes` is the contract, not a malformed record — flagging it would
    false-flag every well-formed read.
    """
    txns = [
        make_transaction(
            vendor="Acme", amount=Decimal("5.00"), date=_day(0), artifact_bytes=b""
        )
    ]
    report = await flag_anomaly(_ledger(txns), make_config(), "2026-Q2")
    assert report.flags == ()


async def test_a_record_can_be_both_duplicate_and_malformed():
    """The checks are orthogonal: a malformed record may also be a duplicate.

    Two blank-vendor, zero-amount, same-date records key together as a duplicate
    *and* each is malformed — both surface independently.
    """
    txns = [
        make_transaction(vendor="", amount=Decimal("0"), date=_day(0)),
        make_transaction(vendor="", amount=Decimal("0"), date=_day(0)),
    ]
    report = await flag_anomaly(_ledger(txns), make_config(), "2026-Q2")
    kinds = _kinds(report)
    assert kinds.count(AnomalyKind.DUPLICATE) == 1  # one group of the two
    assert kinds.count(AnomalyKind.MALFORMED) == 2  # each record malformed


# --- §2 line: mechanical-only, no trend / forecast / predictive -------------


async def test_holds_the_section_2_line_no_trend_or_forecast_detection():
    """A clean-but-"trending" period produces no flags (the §2 line, behaviourally).

    Six records for one vendor with steadily *increasing* amounts over distinct
    dates — a classic "this job is trending over budget" pattern. Each record is
    mechanically clean: well-formed, below the materiality floor, distinct amounts
    (so not duplicates) on distinct dates. flagAnomaly must surface **nothing** —
    it inspects individual records for mechanical defects, never trends across them
    (the §2-excluded predictive / analytics layer).
    """
    txns = [
        make_transaction(vendor="Acme", amount=Decimal(str(100 * n)), date=_day(7 * n))
        for n in range(1, 7)  # 100, 200, ... 600 — all < the 1000 floor
    ]
    report = await flag_anomaly(_ledger(txns), make_config(), "2026-Q2")
    assert report.flags == ()


def test_docstring_documents_mechanical_only_scope():
    """A docstring asserts the scope is mechanical-only (no trend / forecast), per §2.

    `inspect.getmodule` resolves the defining module reliably — `import
    bookkeeper.skills.flag_anomaly as mod` would bind the *function* (it shadows
    the same-named submodule in the package namespace), not the module docstring.
    """
    import inspect

    mod = inspect.getmodule(flag_anomaly)
    raw = mod.__doc__ or ""
    doc = raw.lower()
    assert "mechanical" in doc
    assert "§2" in raw
    for excluded in ("trend", "forecast", "pattern", "predictive"):
        assert excluded in doc, f"docstring should disclaim {excluded!r}"


# --- deterministic ordering --------------------------------------------------


async def test_flags_ordered_by_kind_then_read_order():
    """Flags group by kind in a fixed order: DUPLICATE, OVER_MATERIALITY, MALFORMED."""
    txns = [
        make_transaction(vendor="Acme", amount=Decimal("45.99"), date=_day(0)),  # dup
        make_transaction(vendor="Acme", amount=Decimal("45.99"), date=_day(0)),  # dup
        make_transaction(vendor="BigCo", amount=Decimal("5000.00"), date=_day(0)),  # over-mat
        make_transaction(vendor="", amount=Decimal("3.00"), date=_day(0)),  # malformed
    ]
    report = await flag_anomaly(_ledger(txns), make_config(), "2026-Q2")
    assert _kinds(report) == [
        AnomalyKind.DUPLICATE,
        AnomalyKind.OVER_MATERIALITY,
        AnomalyKind.MALFORMED,
    ]


async def test_multiple_flags_of_a_kind_are_in_ledger_read_order():
    """Within a kind, flags follow ledger read order (here: two over-materiality items)."""
    txns = [
        make_transaction(vendor="First", amount=Decimal("8000.00"), date=_day(0)),
        make_transaction(vendor="Second", amount=Decimal("9000.00"), date=_day(0)),
    ]
    report = await flag_anomaly(_ledger(txns), make_config(), "2026-Q2")
    vendors = [f.transactions[0].vendor for f in report.flags]
    assert vendors == ["First", "Second"]


# --- §5: advisory — writes nothing, never blocks ----------------------------


async def test_writes_nothing_canonical_and_only_reads():
    """§5: flag_anomaly only reads; it stores nothing (advisory, never published).

    Uses the combined read+write ledger fake (one store, both ports) so the proof
    is concrete: after a full run that surfaces flags, no `store` call was made.
    """
    ledger = FakeLedger(
        by_period={
            "2026-Q2": [
                make_transaction(vendor="Acme", amount=Decimal("45.99"), date=_day(0)),
                make_transaction(vendor="Acme", amount=Decimal("45.99"), date=_day(0)),
                make_transaction(vendor="BigCo", amount=Decimal("5000.00"), date=_day(0)),
            ]
        }
    )
    report = await flag_anomaly(ledger, make_config(), "2026-Q2")

    assert len(report.flags) >= 1  # it did surface anomalies...
    assert ledger.store_calls == []  # ...but wrote nothing canonical
    assert ledger.fetched == ["2026-Q2"]  # only read, exactly the period asked for


async def test_returns_advisory_report_never_blocks():
    """A flag is advisory — the skill always returns a report, never raises / gates."""
    ledger = FakeLedger(
        by_period={
            "2026-Q2": [make_transaction(vendor="", amount=Decimal("0"), date=_day(0))]
        }
    )
    report = await flag_anomaly(ledger, make_config(), "2026-Q2")
    assert isinstance(report, AnomalyReport)  # returned, not raised
    assert ledger.store_calls == []
