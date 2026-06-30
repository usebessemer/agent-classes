"""`reconcileAccount` skill tests — §5.5 detection under the §5 boundary.

Each test pins one bullet of the issue's acceptance criteria, plus the lead
review's confirm-tier (Option C):

- `StatementSource` is a read-only port; `StatementLine` is Decimal-money
- `reconcile_account` reads via both ports, sorts every line into exactly one of
  `{matched, to_confirm, gap}`, and **writes nothing canonical** (mutation-proven)
- amount matching is exact `Decimal` equality; the date window is configurable
  with a documented default; vendor fuzzy (`difflib`, normalized) disambiguates;
  matching is one-to-one with deterministic leftover→gap handling
- **vendor-as-confidence (Option C):** an amount+date pair whose vendors diverge
  is *linked but surfaced* `to_confirm`, never silently matched; a mangled-but-
  same vendor auto-matches after normalization; the `reconcile_vendor` floor is a
  §5 boundary, inert-until-configured (unset → lean toward surfacing)
- **every gap surfaced regardless of size** — `materiality_floor` never consulted
- the completeness invariant: every txn and every line lands in exactly one bucket
- the public surface is exported from the package
- deterministic ordering; in-memory fakes only (`FakeStatementSource`)

Money is `Decimal` at the model (exact currency); these fixtures pass `Decimal`
amounts so a difference is a real discrepancy, never float rounding.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from bookkeeper.config import (
    DEFAULT_RECONCILE_DATE_WINDOW_DAYS,
    DEFAULT_RECONCILE_VENDOR_FLOOR,
)
from bookkeeper.ports import StatementSource
from bookkeeper.skills.reconcile import (
    GapKind,
    MatchedPair,
    PairToConfirm,
    ReconciliationReport,
    reconcile_account,
)
from tests.fakes import (
    FakeLedger,
    FakeLedgerSource,
    FakeStatementSource,
    make_config,
    make_statement_line,
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


def _statement(lines, period="2026-Q2"):
    """A `FakeStatementSource` seeded with statement `lines` for one period."""
    return FakeStatementSource(by_period={period: list(lines)})


def _config(*, vendor_floor=DEFAULT_RECONCILE_VENDOR_FLOOR, window_days=None, **overrides):
    """A reconcile-ready config: the vendor confirm-floor live by default.

    `vendor_floor=None` leaves `confidence_thresholds["reconcile_vendor"]` unset,
    i.e. the silent-accept boundary inert (every amount+date pair → `to_confirm`).
    """
    thresholds = {} if vendor_floor is None else {"reconcile_vendor": vendor_floor}
    kw = dict(confidence_thresholds=thresholds)
    if window_days is not None:
        kw["reconcile_date_window_days"] = window_days
    kw.update(overrides)
    return make_config(**kw)


# --- the new port + model ----------------------------------------------------


def test_statement_source_is_read_only():
    """`StatementSource` exposes exactly one abstract method, and it is a read.

    Reconcile is detection-only (§5.5), so the framework ships no statement
    *writer* — there is nothing to mutate the authoritative feed with.
    """
    assert StatementSource.__abstractmethods__ == frozenset({"fetch_statement"})


def test_statement_line_money_is_decimal():
    """`StatementLine` carries `Decimal` money and a stable `statement_ref`."""
    line = make_statement_line(amount=Decimal("12.34"), statement_ref="s-1")
    assert isinstance(line.amount, Decimal)
    assert line.statement_ref == "s-1"  # the traceability handle


def test_reconcile_surface_is_exported_from_package():
    """Change 1: the public surface re-exports through `bookkeeper` (mirror #8/#10)."""
    import bookkeeper

    for name in (
        "reconcile_account",
        "ReconciliationReport",
        "ReconciliationGap",
        "MatchedPair",
        "PairToConfirm",
        "GapKind",
        "StatementSource",
        "StatementLine",
        "RECONCILE_VENDOR_SKILL",
    ):
        assert hasattr(bookkeeper, name), f"{name} not exported from bookkeeper"
        assert name in bookkeeper.__all__, f"{name} missing from __all__"
    from bookkeeper import reconcile_account as ra  # the import path the lead called out

    assert ra is bookkeeper.reconcile_account


# --- exact-Decimal amount matching ------------------------------------------


async def test_exact_amount_and_date_pairs_as_matched():
    """A transaction and a line with equal amount, date, and vendor reconcile as a pair."""
    ledger = _ledger(
        [make_transaction(vendor="Acme Supplies", amount=Decimal("45.99"), date=_day(0))]
    )
    statement = _statement(
        [
            make_statement_line(
                statement_ref="s-1",
                amount=Decimal("45.99"),
                date=_day(0),
                description="Acme Supplies",
            )
        ]
    )

    report = await reconcile_account(ledger, statement, _config(), "2026-Q2")

    assert isinstance(report, ReconciliationReport)
    assert report.period == "2026-Q2"
    assert report.to_confirm == ()
    assert report.gaps == ()
    assert len(report.matched) == 1
    pair = report.matched[0]
    assert isinstance(pair, MatchedPair)
    assert pair.transaction.vendor == "Acme Supplies"
    assert pair.statement_line.statement_ref == "s-1"
    assert ledger.fetched == ["2026-Q2"]  # read exactly the period asked for
    assert statement.fetched == ["2026-Q2"]


async def test_amount_match_is_exact_decimal_equality():
    """`Decimal("10.00")` and `Decimal("10")` are the same money — they pair."""
    ledger = _ledger([make_transaction(vendor="Acme", amount=Decimal("10.00"), date=_day(0))])
    statement = _statement(
        [make_statement_line(amount=Decimal("10"), date=_day(0), description="Acme")]
    )
    report = await reconcile_account(ledger, statement, _config(), "2026-Q2")
    assert len(report.matched) == 1
    assert report.to_confirm == ()
    assert report.gaps == ()


# --- configurable date window (documented default) --------------------------


def test_default_window_is_documented_three_days():
    """The documented default window is ±3 days, surfaced via the config accessor."""
    assert DEFAULT_RECONCILE_DATE_WINDOW_DAYS == 3
    assert make_config().reconcile_date_window() == 3
    assert make_config(reconcile_date_window_days=5).reconcile_date_window() == 5


async def test_date_within_default_window_still_matches():
    """A line that posts two days late still pairs (statements lag — default ±3)."""
    ledger = _ledger([make_transaction(vendor="Acme", amount=Decimal("20.00"), date=_day(0))])
    statement = _statement(
        [make_statement_line(amount=Decimal("20.00"), date=_day(2), description="Acme")]
    )
    report = await reconcile_account(ledger, statement, _config(), "2026-Q2")
    assert len(report.matched) == 1
    assert report.gaps == ()


async def test_date_outside_window_does_not_match():
    """Beyond the window, equal amounts do not pair → two one-sided gaps."""
    ledger = _ledger([make_transaction(vendor="Acme", amount=Decimal("20.00"), date=_day(0))])
    statement = _statement(
        [
            make_statement_line(
                statement_ref="s-1", amount=Decimal("20.00"), date=_day(10), description="Acme"
            )
        ]
    )
    report = await reconcile_account(ledger, statement, _config(), "2026-Q2")
    assert report.matched == ()
    assert report.to_confirm == ()
    assert {g.kind for g in report.gaps} == {
        GapKind.UNMATCHED_IN_LEDGER,
        GapKind.UNMATCHED_ON_STATEMENT,
    }


async def test_date_window_is_configurable():
    """Widening `reconcile_date_window_days` pairs lines a stricter window misses."""
    ledger = _ledger([make_transaction(vendor="Acme", amount=Decimal("20.00"), date=_day(0))])
    statement = _statement(
        [make_statement_line(amount=Decimal("20.00"), date=_day(7), description="Acme")]
    )
    # Default ±3 days → out of range, no link of any kind.
    strict = await reconcile_account(ledger, statement, _config(), "2026-Q2")
    assert strict.matched == ()
    assert strict.to_confirm == ()
    # ±10 days → in range, matches (the control).
    wide = await reconcile_account(ledger, statement, _config(window_days=10), "2026-Q2")
    assert len(wide.matched) == 1
    assert wide.gaps == ()


# --- the three gap kinds -----------------------------------------------------


async def test_statement_line_with_no_ledger_txn_is_unmatched_in_ledger():
    """A statement line with no captured transaction → UNMATCHED_IN_LEDGER gap."""
    statement = _statement([make_statement_line(statement_ref="s-1", amount=Decimal("30.00"))])
    report = await reconcile_account(_ledger([]), statement, _config(), "2026-Q2")

    assert report.matched == ()
    assert report.to_confirm == ()
    assert len(report.gaps) == 1
    gap = report.gaps[0]
    assert gap.kind == GapKind.UNMATCHED_IN_LEDGER
    assert gap.statement_line.statement_ref == "s-1"
    assert gap.transaction is None
    assert gap.delta is None
    assert gap.reason  # carries a §5.5 reason


async def test_ledger_txn_with_no_statement_line_is_unmatched_on_statement():
    """A captured transaction the statement does not show → UNMATCHED_ON_STATEMENT."""
    ledger = _ledger([make_transaction(vendor="Acme", amount=Decimal("30.00"))])
    report = await reconcile_account(ledger, _statement([]), _config(), "2026-Q2")

    assert report.matched == ()
    assert report.to_confirm == ()
    assert len(report.gaps) == 1
    gap = report.gaps[0]
    assert gap.kind == GapKind.UNMATCHED_ON_STATEMENT
    assert gap.transaction.vendor == "Acme"
    assert gap.statement_line is None
    assert gap.delta is None


async def test_amount_mismatch_carries_both_amounts_and_signed_delta():
    """Date + vendor agree, amounts differ → AMOUNT_MISMATCH carrying both + delta."""
    ledger = _ledger(
        [make_transaction(vendor="Acme Supplies", amount=Decimal("45.99"), date=_day(0))]
    )
    statement = _statement(
        [
            make_statement_line(
                statement_ref="s-1",
                amount=Decimal("45.49"),
                date=_day(1),  # within window
                description="Acme Supplies",
            )
        ]
    )
    report = await reconcile_account(ledger, statement, _config(), "2026-Q2")

    assert report.matched == ()
    assert report.to_confirm == ()
    assert len(report.gaps) == 1
    gap = report.gaps[0]
    assert gap.kind == GapKind.AMOUNT_MISMATCH
    assert gap.transaction.amount == Decimal("45.99")
    assert gap.statement_line.amount == Decimal("45.49")
    assert gap.delta == Decimal("0.50")  # ledger - statement, exact Decimal
    assert isinstance(gap.delta, Decimal)


async def test_amount_diff_with_unrelated_vendor_is_two_gaps_not_a_mismatch():
    """Different vendor + different amount on the same date is not one charge.

    Vendor must *agree* for an amount mismatch (the amount can't discriminate);
    unrelated vendors fall through to two separate one-sided gaps.
    """
    ledger = _ledger(
        [make_transaction(vendor="Acme Supplies", amount=Decimal("45.99"), date=_day(0))]
    )
    statement = _statement(
        [
            make_statement_line(
                statement_ref="s-1",
                amount=Decimal("12.00"),
                date=_day(0),
                description="Northwind Traders",
            )
        ]
    )
    report = await reconcile_account(ledger, statement, _config(), "2026-Q2")

    assert all(g.kind != GapKind.AMOUNT_MISMATCH for g in report.gaps)
    assert {g.kind for g in report.gaps} == {
        GapKind.UNMATCHED_IN_LEDGER,
        GapKind.UNMATCHED_ON_STATEMENT,
    }


# --- vendor-as-confidence (Option C) ----------------------------------------


async def test_divergent_vendor_same_amount_date_is_to_confirm_not_matched():
    """A coincidental same-amount/same-date pair from unrelated vendors → to_confirm.

    The case that proves the hole is closed: amount+date alone would absorb this
    as a clean `matched`, burying a genuine missing-entry + duplicate. The vendors
    diverge, so it is *linked but surfaced* for the human, never silently matched.
    """
    ledger = _ledger(
        [make_transaction(vendor="Acme Supplies", amount=Decimal("50.00"), date=_day(0))]
    )
    statement = _statement(
        [
            make_statement_line(
                statement_ref="s-1",
                amount=Decimal("50.00"),
                date=_day(0),
                description="Northwind Traders",
            )
        ]
    )
    report = await reconcile_account(ledger, statement, _config(), "2026-Q2")

    assert report.matched == ()  # not silently matched
    assert report.gaps == ()  # not a false gap either — it is linked
    assert len(report.to_confirm) == 1
    item = report.to_confirm[0]
    assert isinstance(item, PairToConfirm)
    assert item.pair.transaction.vendor == "Acme Supplies"
    assert item.pair.statement_line.statement_ref == "s-1"
    assert item.vendor_similarity < DEFAULT_RECONCILE_VENDOR_FLOOR
    assert item.reason  # carries the §5.5 why


async def test_mangled_but_same_vendor_auto_matches_after_normalization():
    """A processor-mangled descriptor for the same merchant auto-confirms.

    `SQ *JOE'S CAFE 415` and `Joe's Cafe` normalize to the same words, score high,
    and are accepted as a confident match — no false-gap noise from mangling.
    """
    ledger = _ledger(
        [make_transaction(vendor="Joe's Cafe", amount=Decimal("18.50"), date=_day(0))]
    )
    statement = _statement(
        [
            make_statement_line(
                statement_ref="s-1",
                amount=Decimal("18.50"),
                date=_day(1),
                description="SQ *JOE'S CAFE 415",
            )
        ]
    )
    report = await reconcile_account(ledger, statement, _config(), "2026-Q2")

    assert report.to_confirm == ()
    assert report.gaps == ()
    assert len(report.matched) == 1
    assert report.matched[0].statement_line.statement_ref == "s-1"


async def test_inert_unset_floor_surfaces_all_pairs_to_confirm():
    """Inert (no `reconcile_vendor` floor) → every amount+date pair → to_confirm.

    Even an exact-vendor pair is surfaced while the boundary is inert — nothing is
    silently matched before the boundary is configured (§5: inert until configured).
    """
    transactions = [
        make_transaction(vendor="Acme Supplies", amount=Decimal("50.00"), date=_day(0)),
        make_transaction(vendor="Beta Company", amount=Decimal("12.00"), date=_day(0)),
    ]
    lines = [
        make_statement_line(statement_ref="s-1", amount=Decimal("50.00"), date=_day(0), description="Acme Supplies"),
        make_statement_line(statement_ref="s-2", amount=Decimal("12.00"), date=_day(0), description="Beta Company"),
    ]

    inert = await reconcile_account(
        _ledger(transactions), _statement(lines), _config(vendor_floor=None), "2026-Q2"
    )
    assert inert.matched == ()  # nothing silently matched while inert
    assert len(inert.to_confirm) == 2  # both exact-vendor pairs surfaced
    assert all("inert" in c.reason for c in inert.to_confirm)

    # The control: with the floor configured, those same exact-vendor pairs match.
    live = await reconcile_account(
        _ledger(transactions), _statement(lines), _config(), "2026-Q2"
    )
    assert len(live.matched) == 2
    assert live.to_confirm == ()


# --- every gap surfaced regardless of size (no materiality filter) ----------


async def test_tiny_amount_mismatch_is_surfaced_materiality_floor_not_applied():
    """§5.5: a one-cent mismatch is surfaced despite a large `materiality_floor`.

    The config carries `materiality_floor=1000.0`; reconcile must ignore it and
    surface even a one-cent discrepancy (the charter: never silently reconcile a
    mismatch, however small — that floor is `flagAnomaly`'s, not reconcile's).
    """
    config = _config(materiality_floor=1000.0)
    ledger = _ledger(
        [make_transaction(vendor="Acme Supplies", amount=Decimal("100.01"), date=_day(0))]
    )
    statement = _statement(
        [
            make_statement_line(
                statement_ref="s-1",
                amount=Decimal("100.00"),
                date=_day(0),
                description="Acme Supplies",
            )
        ]
    )
    report = await reconcile_account(ledger, statement, config, "2026-Q2")

    assert len(report.gaps) == 1
    gap = report.gaps[0]
    assert gap.kind == GapKind.AMOUNT_MISMATCH
    assert gap.delta == Decimal("0.01")  # one cent, far below the floor — still surfaced


async def test_tiny_unmatched_line_is_surfaced_regardless_of_size():
    """§5.5: even a five-cent unmatched line is surfaced, never size-filtered."""
    config = _config(materiality_floor=1000.0)
    statement = _statement([make_statement_line(statement_ref="s-1", amount=Decimal("0.05"))])
    report = await reconcile_account(_ledger([]), statement, config, "2026-Q2")
    assert len(report.gaps) == 1
    assert report.gaps[0].kind == GapKind.UNMATCHED_IN_LEDGER


# --- one-to-one matching -----------------------------------------------------


async def test_one_line_consumes_only_one_of_two_identical_txns():
    """One-to-one: a single line pairs one of two identical txns; the other is a gap."""
    ledger = _ledger(
        [
            make_transaction(vendor="Acme Supplies", amount=Decimal("45.99"), date=_day(0)),
            make_transaction(vendor="Acme Supplies", amount=Decimal("45.99"), date=_day(0)),
        ]
    )
    statement = _statement(
        [
            make_statement_line(
                statement_ref="s-1",
                amount=Decimal("45.99"),
                date=_day(0),
                description="Acme Supplies",
            )
        ]
    )
    report = await reconcile_account(ledger, statement, _config(), "2026-Q2")

    assert len(report.matched) == 1
    assert len(report.gaps) == 1
    assert report.gaps[0].kind == GapKind.UNMATCHED_ON_STATEMENT


async def test_one_txn_consumes_only_one_of_two_identical_lines():
    """One-to-one, mirror: one txn pairs one of two identical lines; the other is a gap."""
    ledger = _ledger(
        [make_transaction(vendor="Acme Supplies", amount=Decimal("45.99"), date=_day(0))]
    )
    statement = _statement(
        [
            make_statement_line(
                statement_ref="s-1", amount=Decimal("45.99"), date=_day(0), description="Acme Supplies"
            ),
            make_statement_line(
                statement_ref="s-2", amount=Decimal("45.99"), date=_day(0), description="Acme Supplies"
            ),
        ]
    )
    report = await reconcile_account(ledger, statement, _config(), "2026-Q2")

    assert len(report.matched) == 1
    assert len(report.gaps) == 1
    assert report.gaps[0].kind == GapKind.UNMATCHED_IN_LEDGER


async def test_vendor_fuzzy_disambiguates_equal_amount_date_candidates():
    """When several free txns share amount+date, vendor fuzzy picks the right one."""
    ledger = _ledger(
        [
            make_transaction(vendor="Northwind Traders", amount=Decimal("50.00"), date=_day(0)),
            make_transaction(vendor="Acme Supplies", amount=Decimal("50.00"), date=_day(0)),
        ]
    )
    statement = _statement(
        [
            make_statement_line(
                statement_ref="s-1", amount=Decimal("50.00"), date=_day(0), description="Acme Supplies"
            )
        ]
    )
    report = await reconcile_account(ledger, statement, _config(), "2026-Q2")

    assert len(report.matched) == 1
    assert report.matched[0].transaction.vendor == "Acme Supplies"  # vendor disambiguated
    leftovers = [g for g in report.gaps if g.kind == GapKind.UNMATCHED_ON_STATEMENT]
    assert len(leftovers) == 1
    assert leftovers[0].transaction.vendor == "Northwind Traders"


# --- completeness invariant --------------------------------------------------


async def test_every_line_lands_in_exactly_one_bucket():
    """Completeness: every txn and every line is in exactly one of the three buckets.

    A mixed period — confident match, divergent-vendor to_confirm, amount mismatch,
    ledger-only, statement-only — accounts for each side once, nowhere twice.
    """
    transactions = [
        make_transaction(vendor="Acme Supplies", amount=Decimal("45.99"), date=_day(0)),  # matched
        make_transaction(vendor="Acme Supplies", amount=Decimal("50.00"), date=_day(0)),  # to_confirm (divergent line)
        make_transaction(vendor="Beta Company", amount=Decimal("20.00"), date=_day(0)),  # amount mismatch
        make_transaction(vendor="Solo Ledger", amount=Decimal("77.00"), date=_day(0)),  # on-statement gap
    ]
    lines = [
        make_statement_line(statement_ref="m", amount=Decimal("45.99"), date=_day(0), description="Acme Supplies"),
        make_statement_line(statement_ref="c", amount=Decimal("50.00"), date=_day(0), description="Northwind Traders"),
        make_statement_line(statement_ref="x", amount=Decimal("21.00"), date=_day(0), description="Beta Company"),
        make_statement_line(statement_ref="o", amount=Decimal("9.00"), date=_day(0), description="Office Depot"),
    ]
    report = await reconcile_account(_ledger(transactions), _statement(lines), _config(), "2026-Q2")

    matched_txns = [p.transaction for p in report.matched]
    confirm_txns = [c.pair.transaction for c in report.to_confirm]
    gap_txns = [g.transaction for g in report.gaps if g.transaction is not None]
    matched_lines = [p.statement_line for p in report.matched]
    confirm_lines = [c.pair.statement_line for c in report.to_confirm]
    gap_lines = [g.statement_line for g in report.gaps if g.statement_line is not None]

    all_txns = matched_txns + confirm_txns + gap_txns
    all_lines = matched_lines + confirm_lines + gap_lines
    # Every input txn / line accounted for exactly once (count == input, no dupes).
    assert len(all_txns) == len(transactions)
    assert len({id(t) for t in all_txns}) == len(transactions)
    assert len(all_lines) == len(lines)
    assert len({id(line) for line in all_lines}) == len(lines)
    # The buckets landed as designed.
    assert len(report.matched) == 1
    assert len(report.to_confirm) == 1
    assert {g.kind for g in report.gaps} == {
        GapKind.AMOUNT_MISMATCH,
        GapKind.UNMATCHED_IN_LEDGER,
        GapKind.UNMATCHED_ON_STATEMENT,
    }


# --- deterministic ordering --------------------------------------------------


async def test_gaps_are_ordered_deterministically_by_kind():
    """Gaps group by kind: AMOUNT_MISMATCH, UNMATCHED_IN_LEDGER, UNMATCHED_ON_STATEMENT."""
    ledger = _ledger(
        [
            make_transaction(vendor="Acme Supplies", amount=Decimal("45.99"), date=_day(0)),  # matches
            make_transaction(vendor="Beta Company", amount=Decimal("20.00"), date=_day(0)),  # mismatch
            make_transaction(vendor="Solo Ledger", amount=Decimal("77.00"), date=_day(0)),  # on-statement gap
        ]
    )
    statement = _statement(
        [
            make_statement_line(
                statement_ref="s-match", amount=Decimal("45.99"), date=_day(0), description="Acme Supplies"
            ),
            make_statement_line(
                statement_ref="s-mis", amount=Decimal("21.00"), date=_day(0), description="Beta Company"
            ),
            make_statement_line(
                statement_ref="s-only", amount=Decimal("9.00"), date=_day(0), description="Office Depot"
            ),
        ]
    )
    report = await reconcile_account(ledger, statement, _config(), "2026-Q2")

    assert [p.statement_line.statement_ref for p in report.matched] == ["s-match"]
    assert [g.kind for g in report.gaps] == [
        GapKind.AMOUNT_MISMATCH,
        GapKind.UNMATCHED_IN_LEDGER,
        GapKind.UNMATCHED_ON_STATEMENT,
    ]


# --- §5.5: detection-only, writes nothing canonical -------------------------


async def test_writes_nothing_canonical():
    """§5.5: reconcile only reads; it stores nothing (detection-only, never published).

    Uses the combined read+write ledger fake (one store, both ports) so the proof
    is concrete: after a full run with matches and gaps, no `store` call was made.
    """
    ledger = FakeLedger(
        by_period={
            "2026-Q2": [
                make_transaction(vendor="Acme Supplies", amount=Decimal("45.99"), date=_day(0)),
                make_transaction(vendor="Beta Company", amount=Decimal("10.00"), date=_day(0)),
            ]
        }
    )
    statement = _statement(
        [
            make_statement_line(
                statement_ref="s-1", amount=Decimal("45.99"), date=_day(0), description="Acme Supplies"
            ),
            make_statement_line(
                statement_ref="s-2", amount=Decimal("99.00"), date=_day(0), description="Gamma LLC"
            ),
        ]
    )
    report = await reconcile_account(ledger, statement, _config(), "2026-Q2")

    assert len(report.matched) == 1  # it did compute a match...
    assert len(report.gaps) >= 1  # ...and surfaced gaps...
    assert ledger.store_calls == []  # ...but wrote nothing canonical
    assert ledger.fetched == ["2026-Q2"]  # only read
    assert statement.fetched == ["2026-Q2"]


async def test_to_confirm_is_reported_never_auto_resolved():
    """§5.5: a to_confirm pair is surfaced and written nowhere — resolution is human."""
    ledger = FakeLedger(
        by_period={
            "2026-Q2": [make_transaction(vendor="Acme Supplies", amount=Decimal("50.00"), date=_day(0))]
        }
    )
    statement = _statement(
        [
            make_statement_line(
                statement_ref="s-1", amount=Decimal("50.00"), date=_day(0), description="Northwind Traders"
            )
        ]
    )
    report = await reconcile_account(ledger, statement, _config(), "2026-Q2")

    assert len(report.to_confirm) == 1  # surfaced for the human...
    assert ledger.store_calls == []  # ...never auto-resolved or written
    assert ledger.fetched == ["2026-Q2"]
    assert statement.fetched == ["2026-Q2"]


# --- empty period -----------------------------------------------------------


async def test_empty_period_returns_empty_report():
    """A period with no transactions and no statement lines reconciles to empty."""
    report = await reconcile_account(
        FakeLedgerSource(), FakeStatementSource(), _config(), "2026-Q1"
    )
    assert report.matched == ()
    assert report.to_confirm == ()
    assert report.gaps == ()
    assert report.period == "2026-Q1"
