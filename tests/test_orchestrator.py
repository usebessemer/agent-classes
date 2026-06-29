"""§5 boundary tests for `StandingRun`.

Ported and generalized from instance #1's receipt-intake orchestrator tests.
Each test pins one bullet of the §5 contract:

- confident match (boundary configured) → store, mark processed, no review
- unmatched (resolver None) → review + mark processed, never stored
- any exception → review (with partial) + mark processed, never dropped
- store failure → review + mark processed, never dropped or double-filed
- idempotent: a processed item is never fetched again
- inert until configured: an unset attribution threshold routes to review
- Contract B: run log + wake notification are driven correctly
"""

from bookkeeper.contracts import RunOutcome
from bookkeeper.orchestrator import StandingRun
from tests.fakes import (
    FakeAttributionResolver,
    FakeExtractor,
    FakeIntakeSource,
    FakeLedgerSink,
    FakeNotifier,
    FakeReviewQueue,
    FakeRunLog,
    make_config,
    make_item,
)


def _build(
    *,
    items=None,
    extractor=None,
    resolver=None,
    sink=None,
    config=None,
    run_log=None,
    notifier=None,
):
    """Wire a StandingRun from fakes, returning (run, intake, sink, review)."""
    intake = FakeIntakeSource(items=items)
    sink = sink or FakeLedgerSink()
    review = FakeReviewQueue()
    run = StandingRun(
        intake=intake,
        extractor=extractor or FakeExtractor(),
        resolver=resolver or FakeAttributionResolver(),
        sink=sink,
        review_queue=review,
        config=config or make_config(),
        run_log=run_log,
        notifier=notifier,
    )
    return run, intake, sink, review


async def test_confident_match_auto_files_end_to_end():
    """A confident match against an existing target auto-files the transaction."""
    run, intake, sink, review = _build(
        items=[make_item(intake_id="item-001", source_hint="Acme Supplies, May 15")]
    )
    await run.run()

    assert len(sink.stored) == 1
    txn = sink.stored[0]
    assert txn.vendor == "Acme Supplies"
    assert txn.amount == 45.99
    assert txn.tax == 3.50
    assert txn.attribution_target_id == "target-001"
    assert txn.artifact_bytes == b"fake artifact bytes"

    # Marked processed (not re-fetched) and nothing routed to review.
    assert "item-001" in intake.processed_ids
    assert await intake.fetch_items() == []
    assert review.items == []


async def test_handles_multiple_items():
    """Multiple items each flow through the pipeline independently."""
    run, _intake, sink, review = _build(
        items=[
            make_item(intake_id="item-1", source_hint="Supplies"),
            make_item(intake_id="item-2", source_hint="Catering"),
        ]
    )
    await run.run()

    assert len(sink.stored) == 2
    assert review.items == []


async def test_unmatched_routes_to_review_and_marks_processed():
    """Resolver returns None → review + mark processed, never stored (§5.1)."""
    run, intake, sink, review = _build(
        items=[make_item(intake_id="unmatched-001", source_hint="Unknown Project")],
        resolver=FakeAttributionResolver(target_id=None),
    )
    await run.run()

    assert sink.stored == []
    assert len(review.items) == 1
    item, reason, partial = review.items[0]
    assert item.intake_id == "unmatched-001"
    assert "No attribution match" in reason
    assert partial is not None  # the extraction is preserved for the reviewer
    assert "unmatched-001" in intake.processed_ids


async def test_extraction_failure_routes_to_review_with_no_partial():
    """Extraction raises → review (no partial) + mark processed, never dropped."""
    run, intake, sink, review = _build(
        items=[make_item(intake_id="bad-001", source_hint="Unreadable")],
        extractor=FakeExtractor(error=ValueError("unsupported artifact format")),
    )
    await run.run()

    assert sink.stored == []
    assert len(review.items) == 1
    item, reason, partial = review.items[0]
    assert "Processing failed" in reason
    assert "unsupported artifact format" in reason
    assert partial is None  # failed before any extraction was produced
    assert "bad-001" in intake.processed_ids


async def test_storage_failure_routes_to_review_never_drops():
    """Store raises → review (with partial) + mark processed, never dropped."""
    run, intake, sink, review = _build(
        items=[make_item(intake_id="store-fail-001")],
        sink=FakeLedgerSink(error=RuntimeError("ledger unavailable")),
    )
    await run.run()

    assert sink.stored == []
    assert len(review.items) == 1
    _item, reason, partial = review.items[0]
    assert "Storage failed" in reason
    assert partial is not None
    assert "store-fail-001" in intake.processed_ids


async def test_idempotent_across_runs():
    """A processed item is never re-fetched, so a second run is a no-op."""
    run, intake, sink, review = _build(items=[make_item(intake_id="once-001")])
    await run.run()
    await run.run()  # second pass

    assert len(sink.stored) == 1  # not double-filed
    assert intake.processed_ids == {"once-001"}


async def test_inert_until_configured_routes_confident_match_to_review():
    """Unset attribution threshold → even a confident match routes to review.

    This is the safety floor: no instance goes live silently auto-filing before
    its §5 boundary is configured.
    """
    run, intake, sink, review = _build(
        items=[make_item(intake_id="inert-001")],
        resolver=FakeAttributionResolver(target_id="target-001"),  # would match
        config=make_config(confidence_thresholds={}),  # ...but boundary is unset
    )
    await run.run()

    assert sink.stored == []  # not auto-filed
    assert len(review.items) == 1
    _item, reason, _partial = review.items[0]
    assert "not configured" in reason
    assert "inert-001" in intake.processed_ids


async def test_configured_boundary_does_auto_file():
    """Control for the inert test: with the threshold set, the same item files."""
    run, _intake, sink, review = _build(
        items=[make_item(intake_id="live-001")],
        config=make_config(confidence_thresholds={"attribution": 0.9}),
    )
    await run.run()

    assert len(sink.stored) == 1
    assert review.items == []


async def test_run_log_records_every_disposition():
    """Contract B: the run log records auto-filed and routed-to-review outcomes."""
    run_log = FakeRunLog()
    run, _intake, _sink, _review = _build(
        items=[
            make_item(intake_id="filed-1"),
            make_item(intake_id="review-1"),
        ],
        # second item won't match → routed to review
        resolver=_SequenceResolver(["target-001", None]),
        run_log=run_log,
    )
    await run.run()

    by_id = {e.intake_id: e for e in run_log.entries}
    assert by_id["filed-1"].outcome is RunOutcome.AUTO_FILED
    assert by_id["filed-1"].attribution_target_id == "target-001"
    assert by_id["review-1"].outcome is RunOutcome.ROUTED_TO_REVIEW
    assert by_id["review-1"].reason  # carries the §5 reason


async def test_notifier_wakes_only_when_review_items_exist():
    """Contract B: the wake signal fires when (and only when) the queue has items."""
    # Run with one routed item → notifier fires.
    notifier = FakeNotifier()
    run, _intake, _sink, _review = _build(
        items=[make_item(intake_id="review-only")],
        resolver=FakeAttributionResolver(target_id=None),
        notifier=notifier,
    )
    await run.run()
    assert len(notifier.notifications) == 1
    assert "routed to review" in notifier.notifications[0]

    # A clean run (all auto-filed) raises no wake signal.
    notifier2 = FakeNotifier()
    run2, _i2, _s2, _r2 = _build(
        items=[make_item(intake_id="clean")],
        notifier=notifier2,
    )
    await run2.run()
    assert notifier2.notifications == []


class _SequenceResolver(FakeAttributionResolver):
    """Resolver that returns a different target id per call (for mixed-run tests)."""

    def __init__(self, results):
        super().__init__()
        self._results = list(results)
        self._i = 0

    async def resolve(self, transaction, source_hint):
        result = self._results[self._i]
        self._i += 1
        return result
