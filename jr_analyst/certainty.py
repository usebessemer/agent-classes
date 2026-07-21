"""`derive_certainty` — grade a realized actual on the closed-vs-open boundary.

The single most error-prone rule on the certainty ladder, isolated as a **pure,
unit-tested helper in the OSS framework**: given a line's `period` and the last
closed period (`prior_period_state`), decide whether that period is settled
(`realized_closed`) or still in flight (`realized_open`) — or, when the two
labels cannot be ordered at all, return the distinct `CANNOT_ORDER` signal the
caller escalates rather than a silently-wrong grade.

This mirrors the Bookkeeper's own close guard (`_check_period_closeable` /
`_parse_period` in `bookkeeper.skills.close_period`): same two label formats
(`YYYY-Qn` quarterly, `YYYY-MM` monthly), the same parse-and-compare on the
numeric `(year, sub-period)` key that raw string order gets *backwards*
(`"2026-2" > "2026-12"`, `"2026-Q2" > "2026-12"`), and the same fail-safe on an
unparseable or mixed-format pair. The parse is re-implemented here (not imported
from a private skill helper) so the framework's ladder seam stays self-contained
and OSS-pure; the comparison *direction* is the mirror image of the close guard's
— the close guard asks "is this period strictly **after** the last close, so it
may be closed?", this helper asks "is this period **at or before** the last
close, so it is **already** closed?".

The adapter calls this to stamp each `ActualLine.certainty` before the analyst
ever sees the line (see `ports.py`); the analyst reads the grade, it never
decides it — and, being read-only, it still closes nothing. The boundary is the
adapter's to grade and a human's to resolve; this helper only reads two labels.
"""

from __future__ import annotations

import re
from enum import Enum

from jr_analyst.model import Certainty

# The two period-label formats the ladder can order, identical to the Bookkeeper
# close guard's: a quarterly label is ``YYYY-Qn`` (n a single quarter 1–4); a
# monthly label is ``YYYY-MM`` (the month 1–12, padded or not). Anything else does
# not parse — and an unparseable or mixed-format pair yields `CANNOT_ORDER` rather
# than being compared by raw string order (which mis-orders ``2026-2`` vs
# ``2026-12`` and ``2026-Q2`` vs ``2026-Q10``).
_QUARTER_RE = re.compile(r"^(\d{4})-Q([1-4])$")
_MONTH_RE = re.compile(r"^(\d{4})-(\d{1,2})$")


class CannotOrder(Enum):
    """The distinct 'these two periods cannot be ordered' signal — not a grade.

    Returned by `derive_certainty` when a `kind` mismatch (monthly vs quarterly)
    or an unparseable label means the period has **no common order** with the last
    closed period. Grading such a line `realized_closed` or `realized_open` would
    be a guess in either direction, so the helper refuses to tag it and returns
    this loud signal instead — the caller escalates it as
    `UnmappedKind.UNCATEGORIZED_OPEN` (a capture-completeness hole a human
    resolves), **never a silent rung**. A single-member enum so the signal is one
    identity-comparable value (`result is CANNOT_ORDER`) that also lets a type
    checker exhaust the `Certainty | CannotOrder` union.
    """

    #: The one and only `CannotOrder` value (exported as the `CANNOT_ORDER`
    #: module singleton). The string is a stable, readable tag for the run log.
    CANNOT_ORDER = "cannot_order"


#: The single `CannotOrder` value — compare by identity: `result is CANNOT_ORDER`.
CANNOT_ORDER = CannotOrder.CANNOT_ORDER


def _parse_period(label: str | None) -> tuple[str, int, int] | None:
    """Parse a period label into a comparable ``(kind, year, sub)`` key, or `None`.

    Mirrors `bookkeeper.skills.close_period._parse_period`: ``YYYY-Qn`` quarterly →
    ``("Q", year, quarter)`` and ``YYYY-MM`` monthly → ``("M", year, month)`` (the
    month padded or not, validated 1–12). Returns `None` for anything that does not
    match a supported format (e.g. ``2026-Q10``, ``2026-13``, free text, a blank or
    `None` label) — the **fail-safe signal** that this label cannot be ordered
    rather than compared by raw string. Two keys are only orderable when their
    `kind` matches; the caller treats a `None` key or a `kind` mismatch as
    not-comparable and returns `CANNOT_ORDER`.
    """
    text = (label or "").strip()
    quarter = _QUARTER_RE.match(text)
    if quarter:
        return ("Q", int(quarter.group(1)), int(quarter.group(2)))
    month = _MONTH_RE.match(text)
    if month and 1 <= int(month.group(2)) <= 12:
        return ("M", int(month.group(1)), int(month.group(2)))
    return None


def derive_certainty(
    period: str, prior_period_state: str | None
) -> Certainty | CannotOrder:
    """Grade a realized actual's `period` against the last closed period.

    The closed-vs-open boundary as a pure function, for an adapter to stamp onto
    each `ActualLine.certainty`:

    - `prior_period_state` unset (`None`, or blank once stripped — mirroring the
      close guard's "no prior close on record") → **`realized_open`**: nothing has
      closed yet, so the period is necessarily still in flight.
    - Both labels parse and their **kinds match**, and `period` is **at or before**
      the last closed period (``period_key[1:] <= prior_key[1:]``) → the period is
      itself settled → **`realized_closed`**. The compare is on the numeric
      ``(year, sub-period)`` — ``[1:]``, **not** the full 3-tuple, whose leading
      `kind` string would dominate — so `2026-2` orders before `2026-12` (raw
      string gets this backwards). Boundary equality (``period == prior``) is
      closed: the last closed period is, by definition, closed.
    - Both parse, kinds match, but `period` is **strictly after** the last close →
      **`realized_open`**: incurred but not yet closed, the in-flight figure slice
      1 exists to see.
    - **`kind` mismatch (monthly vs quarterly) or an unparseable label →
      `CANNOT_ORDER`**: no common order exists, so the helper refuses to guess a
      rung and returns the distinct signal the caller escalates as
      `UnmappedKind.UNCATEGORIZED_OPEN` — never a silent tag.

    Reads both labels; mutates nothing. Deterministic on its two inputs.
    """
    # Nothing closed yet (unset or blank prior) → the period is necessarily open.
    # An unparseable *period* still falls through to the CANNOT_ORDER guard below;
    # only a missing *prior* means "no close on record", the open default.
    if not (prior_period_state or "").strip():
        return Certainty.REALIZED_OPEN

    period_key = _parse_period(period)
    prior_key = _parse_period(prior_period_state)

    # Cannot order: an unparseable label, or two labels of different kinds
    # (monthly vs quarterly), have no common order — escalate, never guess a rung.
    if period_key is None or prior_key is None or period_key[0] != prior_key[0]:
        return CANNOT_ORDER

    # Same kind, orderable on (year, sub-period). At or before the last close → the
    # period is itself closed; strictly after → still open. Compare ``[1:]`` only.
    if period_key[1:] <= prior_key[1:]:
        return Certainty.REALIZED_CLOSED
    return Certainty.REALIZED_OPEN
