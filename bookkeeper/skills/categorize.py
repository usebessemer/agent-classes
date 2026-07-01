"""`categorizeTransaction` — propose a chart account per transaction (proposals-only v1).

The second computation skill on the §5 core, alongside `track_tax`. It reads a
period of stored transactions and, for each, **proposes** which account in
`config.chart_of_accounts` it belongs to — driven by the **vendor + line-item
description** (what the source artifact *is*), not the source hint (that drove
*attribution*, already done upstream). It returns a *proposed*
`CategorizationReport`; it writes nothing.

Two deliberate differences from `track_tax`: there is **no jurisdiction seam**
(categorization logic is general; the categories come from
`config.chart_of_accounts`), and there is **no money math** (it proposes
accounts, it does not total).

The §5 boundary, preserved exactly (mirrors `track_tax`):

- **Proposals-only / §5.4.** `categorize` *returns* a report; it writes nothing
  to the ledger, the system of record, or the books. Its only ledger-touching
  argument is a read-side `LedgerSource` — there is no sink, no writer in this
  module, so it *cannot* publish. A test pins that it writes nothing canonical.
- **§5.2 — never invent a category.** A transaction whose best match isn't
  confidently in `config.chart_of_accounts` is **flagged**, never given a
  fabricated or newly-created account. The registry never grows itself: a human
  adding a chart entry is a separate human action. The skill only ever proposes
  an account that already exists in the chart.
- **§5.3 — uncertain → review, not a silent guess.** A low-confidence /
  ambiguous transaction becomes a `CategoryFlag`, excluded from the proposals
  (surfaced for a human to confirm/correct), not quietly pre-filled.
- **Inert until configured.** The propose-confidently vs needs-attention cut is
  `config.confidence_thresholds["categorize"]` (mirrors `attribution_threshold`).
  **Unset → conservative:** every transaction is surfaced for human attention,
  none pre-filled as a confident proposal — no instance pre-fills categories
  before its boundary is set.

**The proposal sources (in priority order).** A small, pure proposer
(`_propose`) decides per transaction:

1. **Explicit owner rule** — a vendor→account rule the instance set in
   `config.owner_policies` (the charter's "owner-specific category calls").
   Category rules are namespaced under the `category:` key prefix
   (``"category:home depot" -> "Construction Materials"``) so they never collide
   with other `owner_policies` entries. An exact (normalized) vendor match
   proposes that account at **high confidence**. A rule pointing at an account
   *not* in the chart is flagged (§5.2), never honoured.
2. **Chart match** — a token/fuzzy match of the vendor + description against the
   `chart_of_accounts` account names, scored and **scaled below** owner-rule
   certainty. The best in-chart match wins; ties break on chart order.
3. **No confident match → flag.** Below threshold, or nothing in the chart fits,
   yields a `CategoryFlag` for a human to categorize.

**Honest v1 reality (the matcher is deliberately simple).** A chart account name
rarely appears verbatim in a vendor string ("Home Depot" is not "Construction
Materials"), so the chart-match signal is weak and early proposals will be
rough. That is expected and *why* proposals-only is right: the human
confirm/correct loop is what builds the signal. The framework core is pure
standard library, so the matcher uses `re` + `difflib` here; attribution's
stronger fuzzy matcher lives in its adapter behind the `AttributionResolver`
port, off this framework.

**Two seams, designed but not built in v1** (kept behind `_propose`'s clean
boundary so either can slot in without touching the skill):
- **Precedent.** Once confirmed categories are persisted, "how was this vendor
  categorized before" becomes the strongest signal. Inert now — nothing is
  written back yet (proposals-only writes nothing), so there is no history to
  read.
- **A learned proposer.** A model given vendor + description + the chart could
  replace the heuristic `_match_chart` entirely. `_propose` is the single
  function it would replace.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from bookkeeper.config import BookkeeperConfig
from bookkeeper.model import Transaction
from bookkeeper.ports import LedgerSource

# Which proposal rule fired, recorded on every proposal for traceability
# (charter §1). Plain string tags, not an enum — the framework data model keeps
# category off enums on purpose (categories come from `config.chart_of_accounts`).
SOURCE_OWNER_RULE = "owner-rule"
SOURCE_CHART_MATCH = "chart-match"

# An explicit, human-set owner rule is exact — proposed at full confidence.
_OWNER_RULE_CONFIDENCE = 1.0

# A heuristic chart match is always *less* certain than an explicit owner rule,
# so its raw similarity is scaled under 1.0. This keeps owner-rule proposals
# distinctly the most confident and signals "best-effort guess" honestly.
_CHART_MATCH_CEILING = 0.9

# The `owner_policies` key prefix that namespaces vendor→account category rules,
# so they never collide with other owner-policy entries.
_OWNER_CATEGORY_PREFIX = "category:"

# Split text into describing words on any run of non-alphanumeric characters.
_TOKEN_SPLIT = re.compile(r"[^0-9a-z]+")


# --- The result model (proposed, traceable) --------------------------------


@dataclass(frozen=True)
class CategoryProposal:
    """A **proposed** chart account for one transaction, traceable to its rule.

    `proposed_account` is always an account already present in
    `config.chart_of_accounts` (§5.2: never an invented one). `source` records
    which rule fired (`owner-rule` / `chart-match`) and `confidence` how sure —
    so a human reviewing the proposal can see *why* (charter §1: fully
    traceable). Proposed, never auto-assigned: the assign/write path is a later,
    human-gated step.
    """

    transaction: Transaction
    proposed_account: str
    confidence: float
    source: str


@dataclass(frozen=True)
class CategoryFlag:
    """A transaction the skill could not confidently categorize (§5.2 / §5.3).

    Surfaced for a human to categorize and **excluded from the proposals** —
    needs-a-human, not a fabricated guess. `reason` carries the human-readable
    why (boundary inert, below threshold, nothing in the chart fits, or an owner
    rule pointing outside the chart).
    """

    transaction: Transaction
    reason: str


@dataclass(frozen=True)
class CategorizationReport:
    """A **proposed** per-transaction categorization for a period (charter `categorizeTransaction`).

    Proposed, never published (§5.4): `categorize` returns this; it writes
    nothing to the ledger, the system of record, or the books. Carries the
    confident `proposals` and the `flagged` transactions kept out of them (for
    the human confirm/correct loop). Records the `period` it covers. Ordering is
    deterministic — both tuples preserve the ledger's stable read order.
    """

    period: str
    proposals: tuple[CategoryProposal, ...]
    flagged: tuple[CategoryFlag, ...] = field(default_factory=tuple)


# --- The proposer (the single, replaceable function boundary) ---------------


@dataclass(frozen=True)
class _Candidate:
    """An internal proposed account before the confidence threshold is applied."""

    account: str
    confidence: float
    source: str


def _normalize_vendor(vendor: str) -> str:
    """Normalize a vendor string for owner-rule lookup: lowercase, collapse space."""
    return " ".join((vendor or "").lower().split())


def _owner_rule_account(vendor: str, config: BookkeeperConfig) -> str | None:
    """The account an instance's owner rule maps `vendor` to, or `None`.

    Looks up the `category:`-prefixed, normalized-vendor key in
    `config.owner_policies`. Returns the mapped account name verbatim (membership
    in the chart is checked by the caller, per §5.2), or `None` when no rule fits.
    """
    key = _OWNER_CATEGORY_PREFIX + _normalize_vendor(vendor)
    return config.owner_policies.get(key)


def _tokens(text: str) -> list[str]:
    """Lowercase describing words of `text` (drops separators and empties)."""
    return [t for t in _TOKEN_SPLIT.split(text.lower()) if t]


def _account_tokens(account: str) -> list[str]:
    """Describing words of an account name, dropping pure-digit codes.

    Account names often carry a numeric code ("5000-supplies"); the code is an
    identifier, not a describing word, and never appears in a vendor string, so
    it is dropped for matching.
    """
    return [t for t in _tokens(account) if not t.isdigit()]


def _match_chart(
    vendor: str, description: str, chart: tuple[str, ...]
) -> tuple[str | None, float]:
    """Best token/fuzzy match of vendor + description to a chart account name.

    Returns `(account, raw_score)` for the best in-chart account, or
    `(None, 0.0)` when nothing in the chart shares a word and nothing fuzzily
    resembles it. The score combines:

    - **recall** — the fraction of the account's describing words present in the
      transaction text (catches "office supplies" → "Office Supplies"), and
    - a **fuzzy ratio** over the two token sets (catches near-spellings recall
      misses),

    taking the larger. Ties break on chart order (the first account scanned
    wins), so the result is deterministic for a given chart.
    """
    text_tokens = set(_tokens(f"{vendor} {description}"))
    if not text_tokens:
        return None, 0.0

    text_joined = " ".join(sorted(text_tokens))
    best_account: str | None = None
    best_score = 0.0
    for account in chart:
        acct_tokens = set(_account_tokens(account))
        if not acct_tokens:
            continue
        recall = len(text_tokens & acct_tokens) / len(acct_tokens)
        ratio = SequenceMatcher(None, text_joined, " ".join(sorted(acct_tokens))).ratio()
        score = max(recall, ratio)
        if score > best_score:  # strict > → ties keep the earlier chart entry
            best_account, best_score = account, score
    return best_account, best_score


def _propose(
    transaction: Transaction, config: BookkeeperConfig
) -> tuple[_Candidate | None, str]:
    """Propose an in-chart account for one transaction — the replaceable boundary.

    Returns `(candidate, "")` when an in-chart account is proposable, or
    `(None, reason)` when none is — `reason` is the §1-traceable why a human
    must categorize it. Tries the owner rule first, then the chart match; never
    returns an account outside `config.chart_of_accounts` (§5.2). This is the
    single function a precedent lookup or a learned proposer would replace.
    """
    chart = config.chart_of_accounts
    vendor = transaction.vendor or ""

    # 1. Explicit owner rule — exact, high confidence. Honoured only if it points
    #    at an account that exists in the chart (§5.2: never propose outside it).
    ruled = _owner_rule_account(vendor, config)
    if ruled is not None:
        if ruled in chart:
            return _Candidate(ruled, _OWNER_RULE_CONFIDENCE, SOURCE_OWNER_RULE), ""
        return None, (
            f"Owner category rule maps {vendor!r} to {ruled!r}, which is not in "
            f"chart_of_accounts — needs a human to fix the rule or add the "
            f"account (§5.2: never propose an account outside the chart)."
        )

    # 2. Chart match — best in-chart account, scored and scaled below owner-rule.
    account, raw_score = _match_chart(vendor, transaction.description or "", chart)
    if account is not None and raw_score > 0:
        return _Candidate(account, _CHART_MATCH_CEILING * raw_score, SOURCE_CHART_MATCH), ""

    # 3. Nothing in the chart fits — flag, never fabricate (§5.2).
    return None, (
        "No account in chart_of_accounts matches this transaction's vendor or "
        "description — needs a human to categorize (§5.2: never invent a category)."
    )


# --- The skill operation ----------------------------------------------------


async def categorize(
    ledger_source: LedgerSource,
    config: BookkeeperConfig,
    period: str,
) -> CategorizationReport:
    """Propose a chart account per transaction for `period` — proposed, not assigned.

    1. `fetch_for_period(period)` → the period's stored transactions (read only).
    2. For each, propose via owner-rule → chart-match → flag (`_propose`).
    3. Apply the `categorize` confidence threshold. **Unset → conservative:**
       every transaction is surfaced for attention, none pre-filled (mirrors the
       orchestrator's inert-until-configured floor for attribution).
    4. Return the `CategorizationReport` — proposed for confirm/correct, **never
       auto-assigned.**

    Writes nothing canonical: the only ledger-touching argument is a read-side
    `LedgerSource`. Ordering is deterministic — both tuples preserve the read
    order.
    """
    threshold = config.categorize_threshold()
    transactions = await ledger_source.fetch_for_period(period)

    proposals: list[CategoryProposal] = []
    flagged: list[CategoryFlag] = []
    for transaction in transactions:
        # Inert until configured: with no threshold, surface everything for
        # attention and pre-fill nothing — no auto-confident proposals (§5).
        if threshold is None:
            flagged.append(
                CategoryFlag(
                    transaction,
                    "Categorize boundary not configured (inert) — surfaced for "
                    "human attention rather than pre-filled as a confident "
                    "proposal (§5: inert until configured).",
                )
            )
            continue

        candidate, reason = _propose(transaction, config)
        if candidate is None:
            flagged.append(CategoryFlag(transaction, reason))
            continue

        if candidate.confidence >= threshold:
            proposals.append(
                CategoryProposal(
                    transaction=transaction,
                    proposed_account=candidate.account,
                    confidence=candidate.confidence,
                    source=candidate.source,
                )
            )
        else:
            # §5.3: a sub-threshold match is surfaced, not silently pre-filled.
            flagged.append(
                CategoryFlag(
                    transaction,
                    f"Best match {candidate.account!r} via {candidate.source} "
                    f"scored {candidate.confidence:.2f}, below the categorize "
                    f"threshold {threshold:.2f} — surfaced for human "
                    f"confirm/correct (§5.3).",
                )
            )

    return CategorizationReport(
        period=period,
        proposals=tuple(proposals),
        flagged=tuple(flagged),
    )
