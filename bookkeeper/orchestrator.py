"""The Standing-executor run — the §5 boundary, in code.

`StandingRun` is the intake → extract → attribute → store loop, with the §5
fail-safe as its spine. It is deliberately small: the boundary *is* the product,
so it must be readable in one screen and impossible to weaken by accident.

The §5 contract, preserved exactly:

1. **Confident match → store.** A resolved `attribution_target_id` *and* a
   configured boundary are the only path that auto-files. Nothing else does.
2. **Unmatched (resolver returns `None`) → review + mark-processed.** Never guess.
3. **Any exception → review (with the partial) + mark-processed.** Never drop,
   never re-loop.
4. **Idempotent, and never double-file — even if the mark itself fails.** Store
   and mark-processed are *separate* steps on the auto-file path. A store
   failure routes to review (never drop). A mark failure *after* a successful
   store is logged and left to stand as filed — never re-routed (that would
   double-dispose a filed item), never raised. The item is then re-fetched next
   run, but an idempotent `LedgerSink.store` makes the re-store a no-op, so the
   filing is never duplicated (see `IntakeSource` / `LedgerSink.store`).

Plus the safety floor — **inert until configured**: if the attribution boundary
is not configured (`config.attribution_threshold()` is `None`), even a confident
match routes to review. No instance goes live silently auto-filing.

The class is named `StandingRun` (not `Orchestrator`) because it is the standing
executor's wake-and-run. A generic `StandingExecutor` base could be lifted from
it later when a second class needs one; that is intentionally **not** built now.
"""

from __future__ import annotations

import logging

from bookkeeper.config import BookkeeperConfig
from bookkeeper.contracts import (
    Notifier,
    ReviewQueue,
    RunLog,
    RunLogEntry,
    RunOutcome,
)
from bookkeeper.model import IntakeItem, Transaction
from bookkeeper.ports import AttributionResolver, Extractor, IntakeSource, LedgerSink

logger = logging.getLogger(__name__)


class StandingRun:
    """Runs the end-to-end intake pipeline under the §5 boundary."""

    def __init__(
        self,
        intake: IntakeSource,
        extractor: Extractor,
        resolver: AttributionResolver,
        sink: LedgerSink,
        review_queue: ReviewQueue,
        config: BookkeeperConfig,
        run_log: RunLog | None = None,
        notifier: Notifier | None = None,
    ):
        self.intake = intake
        self.extractor = extractor
        self.resolver = resolver
        self.sink = sink
        self.review_queue = review_queue
        self.config = config
        # Contract B observability — optional so the spine can run bare in tests;
        # wired, they formalize the run log + wake signal instance #1 did ad hoc.
        self.run_log = run_log
        self.notifier = notifier

    async def run(self) -> None:
        """Process every pending intake item once, then raise the wake signal."""
        items = await self.intake.fetch_items()
        filed = 0
        routed = 0
        for item in items:
            outcome = await self._process_item(item)
            if outcome is RunOutcome.AUTO_FILED:
                filed += 1
            else:
                routed += 1

        # Contract B wake notification: only raised when the queue has items.
        if self.notifier is not None and routed:
            await self.notifier.notify(
                f"Bookkeeper run: {filed} auto-filed, {routed} routed to review."
            )

    async def _process_item(self, item: IntakeItem) -> RunOutcome:
        """Process one item through the §5 boundary; return how it was disposed."""
        # Extract + resolve. Any failure here → review with whatever we have.
        partial = None
        try:
            partial = await self.extractor.extract(item.artifact_bytes, item.source_hint)
            target_id = await self.resolver.resolve(partial, item.source_hint)
        except Exception as e:  # noqa: BLE001 — fail-safe floor: never let one item drop
            return await self._route_to_review(item, f"Processing failed: {e}", partial)

        # §5.1 unmatched → review. Never guess a target.
        if target_id is None:
            return await self._route_to_review(
                item, f"No attribution match for: {item.source_hint!r}", partial
            )

        # Safety floor — inert until configured: an unset attribution threshold
        # routes even a confident match to review, so no instance auto-files
        # before its boundary is set.
        if self.config.attribution_threshold() is None:
            return await self._route_to_review(
                item, "Attribution boundary not configured (inert) — routed to review", partial
            )

        # The one auto-file path: confident match against an existing target,
        # boundary configured. Store and mark-processed are two *separately*
        # guarded steps — they must not share a `try`, or a mark failure after a
        # successful store would double-dispose an already-filed item to review.
        #
        # Step 1 — store. A store failure still routes to review (never drop).
        transaction = Transaction(
            attribution_target_id=target_id,
            vendor=partial.vendor,
            amount=partial.amount,
            tax=partial.tax,
            date=partial.date,
            description=partial.description,
            artifact_bytes=item.artifact_bytes,
        )
        try:
            await self.sink.store(transaction)
        except Exception as e:  # noqa: BLE001 — a store failure must not drop the item
            return await self._route_to_review(item, f"Storage failed: {e}", partial)

        # Step 2 — mark processed. The item is now *filed*. A mark failure here
        # is an idempotent-retry concern, NOT a §5 disposition: do not route the
        # filed item to review (that would double-dispose it) and do not raise —
        # log it and let it stand as filed. It will be re-fetched next run (its
        # "marked → never fetched again" guarantee is broken), but an idempotent
        # `LedgerSink.store` makes that re-store a no-op, so it is never
        # duplicated. See `IntakeSource` / `LedgerSink.store` docstrings.
        try:
            await self.intake.mark_processed(item.intake_id)
        except Exception as e:  # noqa: BLE001 — filed already; mark is best-effort retry
            logger.warning(
                "Filed %s but mark_processed failed: %s — item stands as filed; "
                "will be re-fetched next run, idempotent store prevents a duplicate",
                item.intake_id,
                e,
            )

        logger.info("Auto-filed %s to target %s", item.intake_id, target_id)
        await self._record(
            item.intake_id, RunOutcome.AUTO_FILED, attribution_target_id=target_id
        )
        return RunOutcome.AUTO_FILED

    async def _route_to_review(
        self, item: IntakeItem, reason: str, partial
    ) -> RunOutcome:
        """Submit to review and mark processed — the fail-safe disposition."""
        logger.info("Routed %s to review: %s", item.intake_id, reason)
        await self.review_queue.submit(item, reason, partial=partial)
        await self.intake.mark_processed(item.intake_id)
        await self._record(item.intake_id, RunOutcome.ROUTED_TO_REVIEW, reason=reason)
        return RunOutcome.ROUTED_TO_REVIEW

    async def _record(
        self,
        intake_id: str,
        outcome: RunOutcome,
        reason: str = "",
        attribution_target_id: str | None = None,
    ) -> None:
        """Append a structured run-log entry when a `RunLog` is wired (Contract B)."""
        if self.run_log is None:
            return
        await self.run_log.record(
            RunLogEntry(
                intake_id=intake_id,
                outcome=outcome,
                reason=reason,
                attribution_target_id=attribution_target_id,
            )
        )
