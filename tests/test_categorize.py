"""`categorizeTransaction` skill tests — the §4 categorization under the §5 boundary.

Each test pins one bullet of the issue's acceptance criteria:

- `categorize` reads via `LedgerSource`, returns proposals + flagged, writes
  nothing canonical (mutation-proven, like `track_tax`'s §5.4 test)
- proposal priority: owner vendor→account rule (high confidence) → chart
  token/fuzzy match (scaled) → unmatched / below-floor → flagged
- §5.2: never proposes an account outside `config.chart_of_accounts`; a
  transaction matching nothing in the chart is flagged, not fabricated — and an
  owner rule pointing outside the chart is flagged, never honoured
- inert-until-configured: unset `confidence_thresholds["categorize"]` →
  conservative (all surfaced for attention, none pre-filled), with a configured
  control proving the threshold flips it live
- deterministic ordering; reuses the Task-2 `FakeLedgerSource`
"""

import pytest

from bookkeeper.skills.categorize import (
    SOURCE_CHART_MATCH,
    SOURCE_OWNER_RULE,
    CategorizationReport,
    CategoryFlag,
    CategoryProposal,
    categorize,
)
from tests.fakes import FakeLedger, FakeLedgerSource, make_config, make_transaction

# A representative chart of accounts. Account names carry describing words (and,
# for one, a numeric code) so the token/fuzzy matcher can be exercised honestly.
_CHART = (
    "Office Supplies",
    "Construction Materials",
    "Travel and Meals",
    "6000-bank-fees",
)


def _cat_config(*, threshold=0.5, owner_policies=None, chart=_CHART, **overrides):
    """A categorize-ready config: a chart, a threshold (None → inert), owner rules.

    `threshold=None` leaves `confidence_thresholds["categorize"]` unset, i.e. the
    boundary inert. Pass `owner_policies={"category:<vendor>": "<account>"}` to
    seed explicit owner category rules.
    """
    thresholds = {} if threshold is None else {"categorize": threshold}
    return make_config(
        chart_of_accounts=chart,
        confidence_thresholds=thresholds,
        owner_policies=owner_policies or {},
        **overrides,
    )


def _source(transactions, period="2026-Q2"):
    """A `FakeLedgerSource` seeded with `transactions` for one period."""
    return FakeLedgerSource(by_period={period: list(transactions)})


# --- proposal priority: owner rule (high) → chart match (scaled) → flag ------


async def test_owner_rule_proposes_high_confidence():
    """An explicit owner vendor→account rule proposes that account, high confidence."""
    config = _cat_config(
        owner_policies={"category:home depot": "Construction Materials"}
    )
    source = _source(
        [make_transaction(vendor="Home Depot", description="lumber and screws")]
    )

    report = await categorize(source, config, "2026-Q2")

    assert isinstance(report, CategorizationReport)
    assert source.fetched == ["2026-Q2"]  # read exactly the period asked for
    assert report.flagged == ()
    assert len(report.proposals) == 1
    proposal = report.proposals[0]
    assert isinstance(proposal, CategoryProposal)
    assert proposal.proposed_account == "Construction Materials"
    assert proposal.source == SOURCE_OWNER_RULE
    assert proposal.confidence == 1.0  # explicit human rule → full confidence


async def test_owner_rule_vendor_match_is_normalized():
    """Owner-rule matching is case/whitespace-insensitive on the vendor."""
    config = _cat_config(owner_policies={"category:home depot": "Construction Materials"})
    source = _source([make_transaction(vendor="  HOME   Depot ")])
    report = await categorize(source, config, "2026-Q2")
    assert report.proposals[0].proposed_account == "Construction Materials"


async def test_chart_match_proposes_scaled_confidence():
    """A token/fuzzy chart match proposes the best in-chart account, scaled < 1.0."""
    config = _cat_config()  # no owner rules → chart match carries it
    source = _source(
        [make_transaction(vendor="Staples", description="office supplies order")]
    )

    report = await categorize(source, config, "2026-Q2")

    assert len(report.proposals) == 1
    proposal = report.proposals[0]
    assert proposal.proposed_account == "Office Supplies"
    assert proposal.source == SOURCE_CHART_MATCH
    # Scaled strictly below an owner rule's certainty (a best-effort guess).
    assert 0.0 < proposal.confidence < 1.0


async def test_owner_rule_takes_priority_over_chart_match():
    """When both could fire, the explicit owner rule wins (priority order)."""
    # The owner rule names "Office Supplies", but the description would chart-match
    # "Construction Materials". The rule must win.
    config = _cat_config(owner_policies={"category:home depot": "Office Supplies"})
    source = _source(
        [make_transaction(vendor="Home Depot", description="construction materials")]
    )

    report = await categorize(source, config, "2026-Q2")

    proposal = report.proposals[0]
    assert proposal.proposed_account == "Office Supplies"
    assert proposal.source == SOURCE_OWNER_RULE
    assert proposal.confidence == 1.0


async def test_below_threshold_match_is_flagged_not_proposed():
    """§5.3: a match below the categorize threshold is surfaced, not pre-filled."""
    # The Office-Supplies chart match scales to 0.9; a 0.95 threshold cuts it.
    config = _cat_config(threshold=0.95)
    source = _source(
        [make_transaction(vendor="Staples", description="office supplies order")]
    )

    report = await categorize(source, config, "2026-Q2")

    assert report.proposals == ()
    assert len(report.flagged) == 1
    assert "threshold" in report.flagged[0].reason
    # The very same transaction proposes once the threshold admits it (control).
    lenient = await categorize(source, _cat_config(threshold=0.5), "2026-Q2")
    assert lenient.proposals[0].proposed_account == "Office Supplies"


# --- §5.2: never propose an account outside the chart -----------------------


async def test_unmatched_transaction_is_flagged_not_fabricated():
    """§5.2: nothing in the chart fits → flagged for a human, never an invented account."""
    config = _cat_config()
    # Empty vendor + description: no token to match anything in the chart.
    source = _source([make_transaction(vendor="", description="")])

    report = await categorize(source, config, "2026-Q2")

    assert report.proposals == ()
    assert len(report.flagged) == 1
    assert isinstance(report.flagged[0], CategoryFlag)
    assert report.flagged[0].reason  # carries a §5.2 reason


async def test_never_proposes_an_account_outside_the_chart():
    """§5.2 invariant: every proposed account is one already in the chart."""
    config = _cat_config(
        owner_policies={"category:home depot": "Construction Materials"}
    )
    source = _source(
        [
            make_transaction(vendor="Home Depot", description="lumber"),
            make_transaction(vendor="Staples", description="office supplies"),
            make_transaction(vendor="Delta Airlines", description="travel and meals"),
            make_transaction(vendor="", description=""),  # unmatched → flagged
        ]
    )

    report = await categorize(source, config, "2026-Q2")

    assert all(p.proposed_account in _CHART for p in report.proposals)


async def test_owner_rule_outside_chart_is_flagged_never_honoured():
    """§5.2: an owner rule pointing at a non-chart account is flagged, not proposed.

    Even an explicit human rule cannot push an account that isn't in the chart —
    the registry never grows itself; adding the account is a separate human action.
    """
    config = _cat_config(
        owner_policies={"category:home depot": "Owner's Private Account"}
    )
    source = _source([make_transaction(vendor="Home Depot", description="lumber")])

    report = await categorize(source, config, "2026-Q2")

    assert report.proposals == ()
    assert len(report.flagged) == 1
    assert "chart_of_accounts" in report.flagged[0].reason


# --- inert until configured: unset → conservative, configured → live --------


async def test_inert_unset_threshold_surfaces_all_none_prefilled():
    """Unset `categorize` threshold → every transaction flagged, none pre-filled."""
    # An owner-rule vendor (would be 1.0) and a chart-match vendor (would propose)
    # are *both* surfaced for attention while the boundary is inert.
    config = _cat_config(
        threshold=None,
        owner_policies={"category:home depot": "Construction Materials"},
    )
    source = _source(
        [
            make_transaction(vendor="Home Depot", description="lumber"),
            make_transaction(vendor="Staples", description="office supplies"),
            make_transaction(vendor="", description=""),
        ]
    )

    report = await categorize(source, config, "2026-Q2")

    assert report.proposals == ()  # nothing auto-pre-filled
    assert len(report.flagged) == 3  # all surfaced for attention
    assert all("inert" in f.reason for f in report.flagged)


async def test_configured_threshold_pre_fills_confident_proposals():
    """The control: with the threshold set, confident matches become proposals."""
    config = _cat_config(
        threshold=0.5,
        owner_policies={"category:home depot": "Construction Materials"},
    )
    source = _source(
        [
            make_transaction(vendor="Home Depot", description="lumber"),
            make_transaction(vendor="Staples", description="office supplies"),
            make_transaction(vendor="", description=""),  # still unmatched → flagged
        ]
    )

    report = await categorize(source, config, "2026-Q2")

    accounts = {p.proposed_account for p in report.proposals}
    assert accounts == {"Construction Materials", "Office Supplies"}
    assert len(report.flagged) == 1  # only the unmatched one


# --- §5.4: writes nothing canonical -----------------------------------------


async def test_writes_nothing_canonical():
    """§5.4: categorize only reads; it stores nothing (proposed, never assigned).

    Uses the combined read+write fake (one store, both ports) so the proof is
    concrete: after a full run, no `store` call was made.
    """
    ledger = FakeLedger(
        by_period={
            "2026-Q2": [
                make_transaction(vendor="Staples", description="office supplies"),
                make_transaction(vendor="Delta Airlines", description="travel and meals"),
            ]
        }
    )
    report = await categorize(ledger, _cat_config(), "2026-Q2")

    assert len(report.proposals) == 2  # it did compute proposals
    assert ledger.store_calls == []  # ...but wrote nothing canonical
    assert ledger.fetched == ["2026-Q2"]  # only read


# --- deterministic ordering --------------------------------------------------


async def test_ordering_preserves_read_order():
    """Proposals and flags each preserve the ledger's stable read order."""
    transactions = [
        make_transaction(vendor="Staples", description="office supplies"),  # proposal
        make_transaction(vendor="Unknown One", description=""),  # flag
        make_transaction(vendor="Delta Airlines", description="travel and meals"),  # proposal
        make_transaction(vendor="Unknown Two", description=""),  # flag
    ]
    config = _cat_config(threshold=0.5)
    report = await categorize(_source(transactions), config, "2026-Q2")

    assert [p.transaction.vendor for p in report.proposals] == [
        "Staples",
        "Delta Airlines",
    ]
    assert [f.transaction.vendor for f in report.flagged] == [
        "Unknown One",
        "Unknown Two",
    ]


# --- empty period -----------------------------------------------------------


async def test_empty_period_returns_empty_report():
    """A period with no transactions returns an empty, un-flagged report."""
    report = await categorize(FakeLedgerSource(), _cat_config(), "2026-Q1")
    assert report.proposals == ()
    assert report.flagged == ()
    assert report.period == "2026-Q1"
