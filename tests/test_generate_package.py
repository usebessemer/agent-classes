"""`generateAccountantPackage` skill tests — §5.4 assemble-and-propose, never publish.

Each test pins one bullet of the issue's acceptance criteria:

- `generate_accountant_package` returns a *proposed* `AccountantPackage` assembling
  the full Contract A content (costed / categorized / taxed ledger entries with
  source links, tax broken out per target + period, the reconciliation result, the
  period summary), and **writes nothing canonical / calls no writer / SoR**
  (mutation-proven: it takes no write-capable port and references none)
- it **refuses a BLOCKED close** — given a non-`READY` close it produces no
  deliverable, only a blocked result naming the unmet close; never a package over
  an unclosed period (proven both for an open report item and for a clean-reports-
  but-blocked close, so the refusal keys off close readiness, not report content)
- every figure traces to its source / report; ordering is deterministic; the
  package is `accountant_format`-agnostic (the format never changes the output)
- the public surface is exported from the package

`generate_accountant_package` is a **pure function over the close** — no ports, no
`await` — so these tests build the three reports directly, run the real
`close_period` to get a `CloseReport`, then assemble the package from it (no
pipeline run).
"""

import inspect
from decimal import Decimal
from pathlib import Path

import pytest

import bookkeeper
from bookkeeper.skills.categorize import CategorizationReport, CategoryProposal
from bookkeeper.skills.close_period import (
    CHECK_PERIOD_CLOSEABLE,
    CHECK_RECONCILIATION_CLEAN,
    CloseStatus,
    close_period,
)
from bookkeeper.skills.generate_package import (
    AccountantPackage,
    PackageEntry,
    PackageStatus,
    PackageSummary,
    generate_accountant_package,
)
from bookkeeper.skills.reconcile import (
    GapKind,
    MatchedPair,
    ReconciliationGap,
    ReconciliationReport,
)
from bookkeeper.skills.track_tax import TargetTax, TaxSummary
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
    """A reconciliation report carrying one open gap (blocks the close)."""
    line = make_statement_line(statement_ref="s-9", amount=Decimal("30.00"))
    gap = ReconciliationGap(
        kind=GapKind.UNMATCHED_IN_LEDGER,
        reason="Statement line 's-9' has no matching ledger transaction.",
        statement_line=line,
    )
    return ReconciliationReport(period=period, matched=(), to_confirm=(), gaps=(gap,))


def _clean_tax(period=_PERIOD):
    """A tax summary with a per-target total struck and nothing flagged."""
    txn = make_transaction(attribution_target_id="target-001", tax=Decimal("3.50"))
    return TaxSummary(
        period=period,
        regime="HST",
        per_target=(TargetTax("target-001", Decimal("3.50"), (txn,)),),
        period_total=Decimal("3.50"),
        flagged=(),
    )


def _cat(period=_PERIOD, vendors=("Acme Supplies", "Beta Co", "Gamma LLC")):
    """A categorization report: one confident proposal per vendor, nothing flagged.

    Each transaction carries a distinct target + tax so the assembled entries are
    individually traceable; ordering follows the tuple order given here.
    """
    props = tuple(
        CategoryProposal(
            transaction=make_transaction(
                vendor=vendor,
                attribution_target_id=f"target-{i:03d}",
                tax=Decimal("1.00") * (i + 1),
            ),
            proposed_account="5000-supplies",
            confidence=0.95,
            source="chart-match",
        )
        for i, vendor in enumerate(vendors)
    )
    return CategorizationReport(period=period, proposals=props, flagged=())


def _ready_close(period=_PERIOD, config=None, recon=None, tax=None, cat=None):
    """Build a READY `CloseReport` from clean reports via the real `close_period`."""
    recon = recon if recon is not None else _clean_recon(period)
    tax = tax if tax is not None else _clean_tax(period)
    cat = cat if cat is not None else _cat(period)
    config = config if config is not None else make_config()
    report = close_period(recon, tax, cat, config, period)
    assert report.status is CloseStatus.READY, "fixture must be a READY close"
    return report, recon, tax, cat, config


# --- public surface ----------------------------------------------------------


def test_package_surface_is_exported_from_package():
    """AC: the public surface re-exports through `bookkeeper` (the #8/#10/#13 convention)."""
    for name in (
        "generate_accountant_package",
        "AccountantPackage",
        "PackageStatus",
        "PackageEntry",
        "PackageSummary",
    ):
        assert hasattr(bookkeeper, name), f"{name} not exported from bookkeeper"
        assert name in bookkeeper.__all__, f"{name} missing from __all__"
    from bookkeeper import generate_accountant_package as gap

    assert gap is bookkeeper.generate_accountant_package


# --- a READY close assembles the proposed Contract A package -----------------


def test_ready_close_assembles_a_proposed_package():
    """AC: a READY close → a PROPOSED package with the full Contract A content."""
    close, recon, tax, cat, config = _ready_close()
    package = generate_accountant_package(close, config)

    assert isinstance(package, AccountantPackage)
    assert package.period == _PERIOD
    assert package.status is PackageStatus.PROPOSED
    assert package.unmet_close is None

    # The costed / categorized / taxed ledger — one entry per categorized txn.
    assert len(package.entries) == len(cat.proposals)
    assert all(isinstance(e, PackageEntry) for e in package.entries)

    # Tax broken out (per target + period) and the reconciliation result attached
    # verbatim — assembly, not re-formatting (identity, like AssembledPeriod).
    assert package.tax_breakout is tax
    assert package.reconciliation is recon

    # The period summary (the counts).
    assert isinstance(package.summary, PackageSummary)


def test_entries_carry_account_target_tax_and_source_link():
    """AC: every entry assembles account + attribution target + tax + source link."""
    close, _recon, _tax, cat, config = _ready_close()
    package = generate_accountant_package(close, config)

    for entry, proposal in zip(package.entries, cat.proposals):
        # The categorization is held verbatim; the joined figures are first-class.
        assert entry.categorization is proposal
        assert entry.account == proposal.proposed_account
        assert entry.attribution_target_id == proposal.transaction.attribution_target_id
        assert entry.tax == proposal.transaction.tax
        # The source-artifact link travels on the transaction (charter §1).
        assert entry.transaction is proposal.transaction


def test_tax_breakout_carries_per_target_and_period_totals():
    """AC: applicable tax is broken out per target + period (the TaxSummary, verbatim)."""
    close, _recon, tax, _cat, config = _ready_close()
    package = generate_accountant_package(close, config)

    assert package.tax_breakout is tax
    assert package.tax_breakout.per_target == tax.per_target  # per-target totals
    assert package.tax_breakout.period_total == tax.period_total  # period total


def test_summary_counts_match_the_close():
    """AC: the package summary carries the close's disposition counts (none open)."""
    close, _recon, _tax, cat, config = _ready_close()
    package = generate_accountant_package(close, config)

    close_summary = close.proposed_close.summary
    assert package.summary.processed == close_summary.processed == len(cat.proposals)
    assert package.summary.auto_filed == close_summary.auto_filed == len(cat.proposals)
    assert package.summary.reviewed == close_summary.reviewed == 0
    assert package.summary.open == close_summary.open == 0  # PROPOSED ⇒ nothing open


def test_package_stamps_the_generic_basis_not_the_format():
    """AC: the package states its generic §3 basis but carries no `accountant_format`."""
    config = make_config(accounting_method="accrual", jurisdiction="CA-ON")
    close, _recon, _tax, _cat, _config = _ready_close(config=config)
    package = generate_accountant_package(close, config)

    assert package.accounting_method == "accrual"
    assert package.jurisdiction == "CA-ON"
    # The deliverable is format-agnostic — the concrete format is the writer's job.
    assert not hasattr(package, "accountant_format")


# --- §5.4: refuses to package an unclosed (BLOCKED) period -------------------


def test_blocked_close_yields_no_package():
    """AC: a BLOCKED close (open item) → no deliverable, a blocked result naming the close."""
    config = make_config()
    blocked = close_period(_recon_with_gap(), _clean_tax(), _cat(), config, _PERIOD)
    assert blocked.status is CloseStatus.BLOCKED  # an open reconciliation gap blocks

    package = generate_accountant_package(blocked, config)

    assert package.status is PackageStatus.BLOCKED
    assert package.period == _PERIOD
    # No deliverable assembled over an unclosed period.
    assert package.entries == ()
    assert package.summary is None
    assert package.tax_breakout is None
    assert package.reconciliation is None
    # The blocked result names the unmet close (the failed precondition + §5.4).
    assert package.unmet_close is not None
    assert _PERIOD in package.unmet_close
    assert CHECK_RECONCILIATION_CLEAN in package.unmet_close
    assert "§5.4" in package.unmet_close


def test_refusal_keys_off_close_readiness_not_report_cleanliness():
    """§5.4: clean reports but a close BLOCKED on the prior-period guard → still no package.

    Even when every report is clean (it would assemble fine), a close blocked for
    *any* reason yields no deliverable — the refusal is about whether the period is
    closed, never about the reports' content.
    """
    config = make_config(prior_period_state="2026-Q2")  # re-closing a filed period
    blocked = close_period(_clean_recon(), _clean_tax(), _cat(), config, "2026-Q2")
    assert blocked.status is CloseStatus.BLOCKED

    package = generate_accountant_package(blocked, config)

    assert package.status is PackageStatus.BLOCKED
    assert package.entries == ()
    assert package.summary is None
    assert package.unmet_close is not None
    assert CHECK_PERIOD_CLOSEABLE in package.unmet_close


def test_no_published_state_exists():
    """§5.4: there is no agent-publishable state — only PROPOSED and BLOCKED.

    Mirrors `close_period`'s no-CLOSED-status guard: the deliverable is *proposed*
    for sign-off and the gated `PackageWriter`, never published by this skill.
    """
    assert {m.name for m in PackageStatus} == {"PROPOSED", "BLOCKED"}
    close = _ready_close()[0]
    package = generate_accountant_package(close, make_config())
    assert not hasattr(package, "published")
    assert not hasattr(package, "filed")


# --- §5.4: proposes, never publishes; writes nothing canonical ---------------


def test_takes_no_writer_so_cannot_publish():
    """§5.4: the skill accepts no sink / writer / queue — structurally cannot publish.

    The mutation-proof for a pure assembler (mirrors #8/#10/#13/#15's writes-nothing
    test): there is no write-capable port among its arguments, so it *cannot* mutate
    the ledger, the SoR, or the queue, nor render a file — only the close + config.
    """
    params = list(inspect.signature(generate_accountant_package).parameters)
    assert params == ["close_report", "config"]
    forbidden = (
        "sink", "writer", "queue", "ledger", "sor", "notifier", "log",
        "publish", "transmit", "file",
    )
    assert not any(tok in p for p in params for tok in forbidden)


def test_references_no_writer_or_system_of_record():
    """§5.4: the skill's code names no `PackageWriter` / `LedgerSink` / write call.

    Robust source proof: tokenize the module and collect only its `NAME` tokens —
    string and docstring contents (which legitimately reference the gated write
    step by name) tokenize as `STRING`, never `NAME`, so this scans the executable
    code alone. The assembler imports no writer, so no write-capable symbol appears.
    """
    import io
    import tokenize

    import bookkeeper.skills.generate_package as module

    source = Path(module.__file__).read_text(encoding="utf-8")
    names = {
        tok.string
        for tok in tokenize.generate_tokens(io.StringIO(source).readline)
        if tok.type == tokenize.NAME
    }
    forbidden = {"PackageWriter", "write_package", "LedgerSink", "store", "submit"}
    assert not (names & forbidden), f"assembler code must reference no writer: {names & forbidden}"


def test_does_not_mutate_or_rebuild_inputs():
    """§5.4: the close's reports are attached verbatim, never mutated or rebuilt."""
    close, recon, tax, cat, config = _ready_close()
    cat_props_before = cat.proposals
    tax_total_before = tax.period_total

    package = generate_accountant_package(close, config)

    # Reports bundled by identity (not copied / rebuilt), inputs unchanged.
    assert package.tax_breakout is tax
    assert package.reconciliation is recon
    assert cat.proposals is cat_props_before
    assert tax.period_total == tax_total_before


# --- deterministic ordering + format-agnostic --------------------------------


def test_entries_are_ordered_deterministically():
    """AC: entries follow the categorization report's order (ledger read order)."""
    vendors = ("Zeta Z", "Alpha A", "Mu M")  # deliberately not alphabetical
    close, _recon, _tax, cat, config = _ready_close(cat=_cat(vendors=vendors))
    package = generate_accountant_package(close, config)

    assert [e.transaction.vendor for e in package.entries] == list(vendors)


def test_package_is_deterministic():
    """AC: deterministic — identical inputs yield value-equal packages."""
    close, _recon, _tax, _cat, config = _ready_close()
    first = generate_accountant_package(close, config)
    second = generate_accountant_package(close, config)

    assert first == second  # frozen dataclasses compare by value


def test_package_is_accountant_format_agnostic():
    """AC: the `accountant_format` never changes the output — assembly is format-blind.

    Two configs identical but for `accountant_format` produce value-equal packages:
    the skill assembles the general Contract A content and leaves rendering to the
    gated `PackageWriter`, so no QBO / spreadsheet / format specific leaks in.
    """
    recon, tax, cat = _clean_recon(), _clean_tax(), _cat()
    qbo = make_config(accountant_format="qbo-export")
    sheet = make_config(accountant_format="spreadsheet-tab")

    pkg_qbo = generate_accountant_package(close_period(recon, tax, cat, qbo, _PERIOD), qbo)
    pkg_sheet = generate_accountant_package(close_period(recon, tax, cat, sheet, _PERIOD), sheet)

    assert pkg_qbo == pkg_sheet  # the format did not influence the assembled package


@pytest.mark.parametrize("name", ["generate_package.py"])
def test_new_module_is_under_the_cleanliness_scan(name):
    """AC: the public-cleanliness gate covers the new module (it globs the package)."""
    from tests.test_public_cleanliness import _package_sources

    scanned = {p.name for p in _package_sources()}
    assert name in scanned, f"{name} must be scanned by the public-cleanliness gate"
