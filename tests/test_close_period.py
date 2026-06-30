"""`closePeriod` skill tests — §5.7 assemble-and-propose under the §5 boundary.

Each test pins one bullet of the issue's acceptance criteria:

- `close_period` returns a `CloseReport` (`READY`/`BLOCKED` + a full checklist),
  **writes nothing canonical and never signs/marks closed** (it takes no
  sink/writer/queue and never mutates `config`/`prior_period_state`)
- it **blocks on any open item** — an open reconcile gap / `to_confirm`, a
  categorize `flagged`, or a tax `flagged` each yield `BLOCKED` with that item as
  a blocker; a fully-clean period yields `READY` + an assembled proposed close
- the **prior-period guard** refuses a period at or before `prior_period_state`
  and never mutates it
- category **proposals don't block** (the close confirms them); only `flagged`
  does — both directions tested
- `READY` assembles a correct period summary (counts reconcile, none open) + the
  costed/categorized/taxed/reconciled period; deterministic
- the public surface is exported from the package

`close_period` is a **pure function over the reports** — no ports, no `await` — so
these tests build the three reports directly (deterministic, no pipeline run).
"""

import inspect
from decimal import Decimal

import pytest

import bookkeeper
from bookkeeper.skills.categorize import (
    CategorizationReport,
    CategoryFlag,
    CategoryProposal,
)
from bookkeeper.skills.close_period import (
    CHECK_CATEGORIZATION_COMPLETE,
    CHECK_PERIOD_CLOSEABLE,
    CHECK_PERIOD_COHERENT,
    CHECK_RECONCILIATION_CLEAN,
    CHECK_TAX_CLEAN,
    AssembledPeriod,
    CloseReport,
    CloseStatus,
    ProposedClose,
    close_period,
)
from bookkeeper.skills.reconcile import (
    GapKind,
    MatchedPair,
    PairToConfirm,
    ReconciliationGap,
    ReconciliationReport,
)
from bookkeeper.skills.track_tax import TargetTax, TaxFlag, TaxSummary
from tests.fakes import make_config, make_statement_line, make_transaction

_PERIOD = "2026-Q2"


# --- report builders (deterministic, no pipeline run) ------------------------


def _clean_recon(period=_PERIOD):
    """A reconciliation report with one confident match and nothing open."""
    txn = make_transaction(vendor="Acme Supplies", amount=Decimal("45.99"))
    line = make_statement_line(statement_ref="s-1", amount=Decimal("45.99"), description="Acme Supplies")
    return ReconciliationReport(
        period=period, matched=(MatchedPair(txn, line),), to_confirm=(), gaps=()
    )


def _recon_with_gap(period=_PERIOD):
    """A reconciliation report carrying one open gap."""
    line = make_statement_line(statement_ref="s-9", amount=Decimal("30.00"))
    gap = ReconciliationGap(
        kind=GapKind.UNMATCHED_IN_LEDGER,
        reason="Statement line 's-9' has no matching ledger transaction.",
        statement_line=line,
    )
    return ReconciliationReport(period=period, matched=(), to_confirm=(), gaps=(gap,))


def _recon_with_to_confirm(period=_PERIOD):
    """A reconciliation report carrying one pair awaiting human confirm/reject."""
    txn = make_transaction(vendor="Acme Supplies", amount=Decimal("50.00"))
    line = make_statement_line(statement_ref="s-2", amount=Decimal("50.00"), description="Northwind Traders")
    pair = PairToConfirm(
        pair=MatchedPair(txn, line),
        vendor_similarity=0.2,
        reason="Amount and date agree but vendors diverge — surfaced for confirm.",
    )
    return ReconciliationReport(period=period, matched=(), to_confirm=(pair,), gaps=())


def _clean_tax(period=_PERIOD):
    """A tax summary with per-target totals struck and nothing flagged."""
    txn = make_transaction(attribution_target_id="target-001", tax=Decimal("3.50"))
    return TaxSummary(
        period=period,
        regime="HST",
        per_target=(TargetTax("target-001", Decimal("3.50"), (txn,)),),
        period_total=Decimal("3.50"),
        flagged=(),
    )


def _tax_with_flag(period=_PERIOD):
    """A tax summary carrying one flagged exception (held out of the totals)."""
    txn = make_transaction(attribution_target_id="", tax=Decimal("2.00"))
    return TaxSummary(
        period=period,
        regime="HST",
        per_target=(),
        period_total=Decimal("0"),
        flagged=(TaxFlag(txn, "Captured tax with no resolvable attribution target (§5.3)."),),
    )


def _clean_cat(period=_PERIOD, proposals=2):
    """A categorization report with `proposals` confident proposals, nothing flagged."""
    props = tuple(
        CategoryProposal(
            transaction=make_transaction(vendor=f"Vendor {i}"),
            proposed_account="5000-supplies",
            confidence=0.95,
            source="chart-match",
        )
        for i in range(proposals)
    )
    return CategorizationReport(period=period, proposals=props, flagged=())


def _cat_with_flag(period=_PERIOD):
    """A categorization report carrying one un-categorizable (flagged) transaction."""
    txn = make_transaction(vendor="Mystery Vendor")
    return CategorizationReport(
        period=period,
        proposals=(),
        flagged=(CategoryFlag(txn, "No account in chart_of_accounts matches (§5.2)."),),
    )


def _close(reconciliation=None, tax=None, categorization=None, config=None, period=_PERIOD):
    """Run `close_period` with sensible all-clean defaults; override any piece."""
    return close_period(
        reconciliation if reconciliation is not None else _clean_recon(period),
        tax if tax is not None else _clean_tax(period),
        categorization if categorization is not None else _clean_cat(period),
        config if config is not None else make_config(),
        period,
    )


def _check(report, name):
    """The single checklist entry with `name` (the checklist always carries all four)."""
    matches = [c for c in report.checklist if c.name == name]
    assert len(matches) == 1, f"expected exactly one {name!r} check, got {len(matches)}"
    return matches[0]


# --- public surface ----------------------------------------------------------


def test_close_surface_is_exported_from_package():
    """AC: the public surface re-exports through `bookkeeper` (the #8/#10/#13 convention)."""
    for name in (
        "close_period",
        "CloseReport",
        "CloseStatus",
        "CloseCheck",
        "CloseBlocker",
        "PeriodSummary",
        "AssembledPeriod",
        "ProposedClose",
    ):
        assert hasattr(bookkeeper, name), f"{name} not exported from bookkeeper"
        assert name in bookkeeper.__all__, f"{name} missing from __all__"
    from bookkeeper import close_period as cp

    assert cp is bookkeeper.close_period


# --- a clean period is READY -------------------------------------------------


def test_clean_period_is_ready_with_assembled_close():
    """A fully-clean period → READY, every check met, an assembled proposed close."""
    report = _close()

    assert isinstance(report, CloseReport)
    assert report.period == _PERIOD
    assert report.status is CloseStatus.READY
    assert report.blockers == ()
    assert len(report.checklist) == 5
    assert all(c.met for c in report.checklist)
    assert all(c.reason for c in report.checklist)  # every check is §1-traceable

    close = report.proposed_close
    assert isinstance(close, ProposedClose)
    assert isinstance(close.assembled, AssembledPeriod)


# --- blocks on any open item -------------------------------------------------


def test_open_reconcile_gap_blocks():
    """AC: an open reconciliation gap → BLOCKED, with that gap as a blocker."""
    recon = _recon_with_gap()
    report = _close(reconciliation=recon)

    assert report.status is CloseStatus.BLOCKED
    assert report.proposed_close is None  # never a signable close over an open item
    assert not _check(report, CHECK_RECONCILIATION_CLEAN).met
    gap_blockers = [b for b in report.blockers if b.check == CHECK_RECONCILIATION_CLEAN]
    assert len(gap_blockers) == 1
    assert gap_blockers[0].item is recon.gaps[0]  # the specific open item, traceable


def test_open_to_confirm_pair_blocks():
    """AC: a reconciliation `to_confirm` pair → BLOCKED, with that pair as a blocker."""
    recon = _recon_with_to_confirm()
    report = _close(reconciliation=recon)

    assert report.status is CloseStatus.BLOCKED
    assert report.proposed_close is None
    assert not _check(report, CHECK_RECONCILIATION_CLEAN).met
    blockers = [b for b in report.blockers if b.check == CHECK_RECONCILIATION_CLEAN]
    assert len(blockers) == 1
    assert blockers[0].item is recon.to_confirm[0]


def test_tax_flag_blocks():
    """AC: a flagged tax exception → BLOCKED, with that flag as a blocker."""
    tax = _tax_with_flag()
    report = _close(tax=tax)

    assert report.status is CloseStatus.BLOCKED
    assert report.proposed_close is None
    assert not _check(report, CHECK_TAX_CLEAN).met
    blockers = [b for b in report.blockers if b.check == CHECK_TAX_CLEAN]
    assert len(blockers) == 1
    assert blockers[0].item is tax.flagged[0]


def test_multiple_open_items_all_surface_as_blockers():
    """Every open item is surfaced — blockers accumulate across reports, never just the first."""
    report = _close(reconciliation=_recon_with_gap(), tax=_tax_with_flag())

    assert report.status is CloseStatus.BLOCKED
    checks_failed = {b.check for b in report.blockers}
    assert checks_failed == {CHECK_RECONCILIATION_CLEAN, CHECK_TAX_CLEAN}
    # The checklist still carries all five, the clean ones met.
    assert len(report.checklist) == 5
    assert _check(report, CHECK_CATEGORIZATION_COMPLETE).met
    assert _check(report, CHECK_PERIOD_CLOSEABLE).met
    assert _check(report, CHECK_PERIOD_COHERENT).met


# --- category proposals don't block; only `flagged` does ---------------------


def test_category_proposals_do_not_block():
    """AC: a period whose only categorization output is proposals (no flags) → READY.

    The human signing the close confirms the proposals; they are not a blocker.
    """
    report = _close(categorization=_clean_cat(proposals=5))

    assert report.status is CloseStatus.READY
    assert _check(report, CHECK_CATEGORIZATION_COMPLETE).met
    assert report.proposed_close is not None


def test_category_flag_blocks():
    """AC: a flagged (un-categorizable) transaction → BLOCKED, with that flag as a blocker."""
    cat = _cat_with_flag()
    report = _close(categorization=cat)

    assert report.status is CloseStatus.BLOCKED
    assert report.proposed_close is None
    assert not _check(report, CHECK_CATEGORIZATION_COMPLETE).met
    blockers = [b for b in report.blockers if b.check == CHECK_CATEGORIZATION_COMPLETE]
    assert len(blockers) == 1
    assert blockers[0].item is cat.flagged[0]


# --- prior-period guard ------------------------------------------------------


def test_period_after_prior_close_is_closeable():
    """A period strictly after the last close is closeable (the control)."""
    config = make_config(prior_period_state="2026-Q1")
    report = _close(config=config, period="2026-Q2")

    assert report.status is CloseStatus.READY
    assert _check(report, CHECK_PERIOD_CLOSEABLE).met


def test_period_at_prior_close_is_refused():
    """AC: a period equal to the last close is refused — never re-close a filed period."""
    config = make_config(prior_period_state="2026-Q2")
    report = _close(config=config, period="2026-Q2")

    assert report.status is CloseStatus.BLOCKED
    assert report.proposed_close is None
    assert not _check(report, CHECK_PERIOD_CLOSEABLE).met
    blockers = [b for b in report.blockers if b.check == CHECK_PERIOD_CLOSEABLE]
    assert len(blockers) == 1
    assert blockers[0].item is None  # the guard is about the period, not a report item


def test_period_before_prior_close_is_refused():
    """A period before the last close is refused too (at-or-before → BLOCKED)."""
    config = make_config(prior_period_state="2026-Q2")
    report = _close(config=config, period="2026-Q1")

    assert report.status is CloseStatus.BLOCKED
    assert not _check(report, CHECK_PERIOD_CLOSEABLE).met


@pytest.mark.parametrize(
    "period,prior,why",
    [
        ("2026-2", "2026-12", "unpadded month: Feb is before Dec"),
        ("2026-02", "2026-12", "padded month: Feb is before Dec"),
        ("2026-3", "2026-03", "padding mismatch: same month (equal → block)"),
        ("2026-Q2", "2026-Q4", "quarter: Q2 is before Q4"),
        ("2026-Q2", "2026-Q10", "multi-digit quarter label does not parse"),
        ("2026-Q2", "2026-05", "mixed quarter vs month — not comparable"),
        ("2026-05", "2026-Q2", "mixed month vs quarter — not comparable"),
        ("2026-13", "2026-12", "invalid month 13 does not parse"),
        ("garbage", "2026-Q1", "unparseable period label"),
        ("2026-Q1", "garbage", "unparseable prior label"),
    ],
)
def test_prior_period_guard_blocks_wrong_direction_or_unparseable(period, prior, why):
    """AC (critical fix): the re-close direction and unparseable labels BLOCK.

    Raw string order would (wrongly) allow several of these; parse-and-compare
    with a fail-safe BLOCK on any label that does not parse to a supported format
    (or two formats that cannot be ordered) refuses every one (§5: never re-close
    or edit a filed prior period, never guess the direction). `why` documents the
    case.
    """
    config = make_config(prior_period_state=prior)
    report = _close(config=config, period=period)

    assert report.status is CloseStatus.BLOCKED, why
    assert report.proposed_close is None, why
    assert not _check(report, CHECK_PERIOD_CLOSEABLE).met, why
    blockers = [b for b in report.blockers if b.check == CHECK_PERIOD_CLOSEABLE]
    assert len(blockers) == 1, why


@pytest.mark.parametrize(
    "period,prior",
    [
        ("2026-12", "2026-2"),  # monthly, unpadded prior: Dec after Feb (raw string mis-orders!)
        ("2026-12", "2026-11"),  # monthly, padded
        ("2026-Q4", "2026-Q1"),  # quarterly, same year
        ("2027-Q1", "2026-Q4"),  # quarterly, year rollover
    ],
)
def test_period_strictly_after_prior_is_closeable(period, prior):
    """The forward direction still passes — parse-and-compare orders numerically.

    `2026-12` after `2026-2` is the case raw string order gets *backwards*
    (``"2026-12" < "2026-2"``): the parsed comparison closes it correctly.
    """
    config = make_config(prior_period_state=prior)
    report = _close(config=config, period=period)

    assert _check(report, CHECK_PERIOD_CLOSEABLE).met
    assert report.status is CloseStatus.READY


def test_unset_prior_period_state_means_any_period_closeable():
    """No prior close on record (unset) → the period passes the prior-state guard."""
    config = make_config()  # prior_period_state defaults to None
    assert config.prior_period_state is None
    report = _close(config=config, period="2026-Q1")

    assert _check(report, CHECK_PERIOD_CLOSEABLE).met
    assert report.status is CloseStatus.READY


def test_prior_period_guard_never_mutates_config_or_prior_state():
    """AC: the guard reads `prior_period_state`; it never mutates `config`/prior state."""
    config = make_config(prior_period_state="2026-Q2")
    before_prior = config.prior_period_state
    before_thresholds = dict(config.confidence_thresholds)

    # Run both a refused and an allowed close against the same config.
    _close(config=config, period="2026-Q2")
    _close(config=make_config(prior_period_state="2026-Q1"), period="2026-Q2")

    assert config.prior_period_state == before_prior == "2026-Q2"
    assert dict(config.confidence_thresholds) == before_thresholds


# --- report period coherence (close the right period's data) -----------------


def test_coherent_reports_pass_the_coherence_check():
    """All three reports describing the close period → the coherence check is met."""
    report = _close()
    assert _check(report, CHECK_PERIOD_COHERENT).met


@pytest.mark.parametrize(
    "recon_period,tax_period,cat_period",
    [
        ("2026-Q1", "2026-Q2", "2026-Q2"),  # reconciliation diverges
        ("2026-Q2", "2026-Q1", "2026-Q2"),  # tax diverges
        ("2026-Q2", "2026-Q2", "2026-Q1"),  # categorization diverges
    ],
)
def test_reports_for_another_period_block(recon_period, tax_period, cat_period):
    """MINOR fix: clean reports for another period → BLOCKED, never a wrong-period close.

    Even with every content check clean, a report whose `.period` differs from the
    period being closed would assemble a wrong-period sign-off proposal — refused.
    """
    report = close_period(
        _clean_recon(recon_period),
        _clean_tax(tax_period),
        _clean_cat(cat_period),
        make_config(),
        "2026-Q2",
    )

    assert report.status is CloseStatus.BLOCKED
    assert report.proposed_close is None
    assert not _check(report, CHECK_PERIOD_COHERENT).met
    blockers = [b for b in report.blockers if b.check == CHECK_PERIOD_COHERENT]
    assert len(blockers) == 1  # one coherence blocker, naming the divergence


# --- READY assembles a correct, deterministic period summary -----------------


def test_ready_summary_counts_reconcile_and_none_open():
    """AC: READY assembles a correct period summary — counts reconcile, none open."""
    report = _close(categorization=_clean_cat(proposals=3))
    summary = report.proposed_close.summary

    assert summary.open == 0  # READY guarantees nothing outstanding
    assert summary.processed == summary.auto_filed + summary.reviewed  # counts reconcile
    assert summary.processed == 3  # the three categorized transactions
    assert summary.auto_filed == 3  # confident proposals
    assert summary.reviewed == 0  # nothing flagged in a clean period


def test_ready_assembles_the_costed_categorized_taxed_reconciled_period():
    """AC: READY assembles the reconciled / taxed / categorized period verbatim."""
    recon, tax, cat = _clean_recon(), _clean_tax(), _clean_cat()
    report = close_period(recon, tax, cat, make_config(), _PERIOD)

    assembled = report.proposed_close.assembled
    assert assembled.period == _PERIOD
    assert assembled.reconciliation is recon  # bundled, not rebuilt
    assert assembled.tax_summary is tax
    assert assembled.categorization is cat


def test_close_period_is_deterministic():
    """AC: deterministic — identical inputs yield equal reports (value-equal)."""
    recon, tax, cat, config = _clean_recon(), _clean_tax(), _clean_cat(), make_config()
    first = close_period(recon, tax, cat, config, _PERIOD)
    second = close_period(recon, tax, cat, config, _PERIOD)

    assert first == second  # frozen dataclasses compare by value


def test_blocked_report_still_carries_full_checklist():
    """A BLOCKED report still reports every precondition (the full picture, not just failures)."""
    report = _close(categorization=_cat_with_flag())

    names = [c.name for c in report.checklist]
    assert names == [
        CHECK_PERIOD_CLOSEABLE,
        CHECK_PERIOD_COHERENT,
        CHECK_RECONCILIATION_CLEAN,
        CHECK_CATEGORIZATION_COMPLETE,
        CHECK_TAX_CLEAN,
    ]  # deterministic order, all five present


# --- §5.7: proposes, never signs; writes nothing canonical -------------------


def test_takes_no_writer_so_cannot_publish():
    """§5.7: `close_period` accepts no sink/writer/queue — structurally cannot publish.

    The mutation-proof for a pure assembler (mirrors #8/#10/#13's writes-nothing
    test): there is no write-capable port among its arguments, so it *cannot*
    mutate the ledger, the SoR, or the queue — only the three read-only reports +
    config + period.
    """
    params = list(inspect.signature(close_period).parameters)
    assert params == ["reconciliation", "tax_summary", "categorization", "config", "period"]
    forbidden = ("sink", "writer", "queue", "ledger", "package", "notifier", "log")
    assert not any(tok in p for p in params for tok in forbidden)


def test_never_signs_no_closed_status_exists():
    """§5.7: there is no agent-signable state — only READY (proposed) and BLOCKED."""
    assert {m.name for m in CloseStatus} == {"READY", "BLOCKED"}
    # A READY result is a *proposal* awaiting a human's signature, never a sign.
    close = _close().proposed_close
    assert not hasattr(close, "signed")
    assert not hasattr(close, "closed")


def test_does_not_mutate_input_reports():
    """§5.7: the input reports are returned bundled, never mutated (they are frozen)."""
    recon, tax, cat = _clean_recon(), _clean_tax(), _clean_cat()
    recon_matched, tax_total, cat_props = recon.matched, tax.period_total, cat.proposals

    close_period(recon, tax, cat, make_config(), _PERIOD)

    # Unchanged after the call — the reports are immutable inputs, not scratch space.
    assert recon.matched is recon_matched
    assert tax.period_total == tax_total
    assert cat.proposals is cat_props
