"""`derive_certainty` tests — the closed-vs-open boundary as a pure helper.

Each test pins one bullet of issue #4's acceptance criteria:

- `derive_certainty(period, prior_period_state)` returns a `Certainty` or the
  distinct **cannot-order** signal (`CANNOT_ORDER`), never a silent tag.
- `prior_period_state is None` → `realized_open` (nothing closed yet).
- Both parse, **kinds match**, and `period_key[1:] <= prior_key[1:]` →
  `realized_closed`; strictly after → `realized_open`. The compare is on the
  numeric ``(year, sub-period)`` — **not** the full 3-tuple — so `2026-2` orders
  before `2026-12` (the trap raw string order gets backwards).
- **kind mismatch (M vs Q) or an unparseable label → `CANNOT_ORDER`**, the signal
  the caller escalates as `uncategorized_open`.
- the named traps: the `2026-Q2` vs `2026-12` mis-order, the M-vs-Q mismatch,
  unset prior, and boundary equality (`period == prior` → closed).
- the helper is on the package's public surface.

`derive_certainty` is a pure function over two strings — no ports, no `await`, no
state — so these tests call it directly.
"""

import pytest

import jr_analyst
from jr_analyst.certainty import CANNOT_ORDER, CannotOrder, derive_certainty
from jr_analyst.model import Certainty


# --- unset prior: nothing closed yet → open ----------------------------------


@pytest.mark.parametrize(
    "period",
    ["2026-Q2", "2026-05", "2027-Q1", "garbage", "", "2026-Q10"],
)
def test_unset_prior_is_realized_open(period):
    """AC: `prior_period_state is None` → `realized_open`, whatever the period.

    With no close on record the period is necessarily still in flight, so the
    unset-prior branch short-circuits *before* the period is even parsed — even an
    unparseable period label grades open (it is never escalated on the prior's
    account).
    """
    assert derive_certainty(period, None) is Certainty.REALIZED_OPEN


@pytest.mark.parametrize("prior", ["", "   ", "\t"])
def test_blank_prior_mirrors_unset(prior):
    """A blank/whitespace prior means "no prior close on record", like the close guard.

    Mirrors `bookkeeper.skills.close_period._check_period_closeable`, which treats a
    blank `prior_period_state` as unset → open, not as an unparseable escalation.
    """
    assert derive_certainty("2026-Q2", prior) is Certainty.REALIZED_OPEN


# --- same kind, at or before the last close → closed -------------------------


@pytest.mark.parametrize(
    "period,prior,why",
    [
        ("2026-Q1", "2026-Q2", "quarter: Q1 is before Q2"),
        ("2026-Q4", "2027-Q1", "quarter: prior year is before next year"),
        ("2026-2", "2026-12", "MIS-ORDER TRAP: Feb before Dec (raw string says after)"),
        ("2026-02", "2026-12", "padded month: Feb before Dec"),
        ("2026-11", "2026-12", "month: Nov before Dec"),
        ("2025-06", "2026-06", "month: prior year before same month next year"),
    ],
)
def test_period_at_or_before_prior_close_is_realized_closed(period, prior, why):
    """AC: kinds match and `period_key[1:] <= prior_key[1:]` → `realized_closed`.

    The period sits at or before the last closed period, so it is itself settled.
    `2026-2` vs `2026-12` is the case raw string order gets *backwards*
    (``"2026-2" > "2026-12"``): parse-and-compare on the numeric key closes it
    correctly. `why` documents each case.
    """
    assert derive_certainty(period, prior) is Certainty.REALIZED_CLOSED, why


@pytest.mark.parametrize(
    "period,prior,why",
    [
        ("2026-Q2", "2026-Q2", "equal quarter → closed"),
        ("2026-3", "2026-03", "padding-insensitive equality → closed"),
        ("2026-03", "2026-3", "padding-insensitive equality (reversed) → closed"),
    ],
)
def test_boundary_equality_is_realized_closed(period, prior, why):
    """AC (named trap): `period == prior` → `realized_closed`.

    The last closed period is, by definition, closed — equality lands on the
    closed side of the boundary (the `<=`). Padding does not change the numeric
    key, so `2026-3` and `2026-03` compare equal.
    """
    assert derive_certainty(period, prior) is Certainty.REALIZED_CLOSED, why


# --- same kind, strictly after the last close → open -------------------------


@pytest.mark.parametrize(
    "period,prior,why",
    [
        ("2026-Q3", "2026-Q2", "quarter: Q3 after Q2"),
        ("2027-Q1", "2026-Q4", "quarter: year rollover, Q1 after prior Q4"),
        ("2026-12", "2026-2", "MIS-ORDER TRAP: Dec after Feb (raw string says before)"),
        ("2026-12", "2026-11", "month: Dec after Nov"),
        ("2026-01", "2025-12", "month: Jan after prior Dec across the year"),
    ],
)
def test_period_strictly_after_prior_close_is_realized_open(period, prior, why):
    """AC: kinds match but `period` is strictly after the last close → `realized_open`.

    Incurred but not yet closed — the in-flight figure slice 1 exists to see.
    `2026-12` vs `2026-2` is the raw-string trap in the other direction
    (``"2026-12" < "2026-2"``): the parsed comparison keeps it open.
    """
    assert derive_certainty(period, prior) is Certainty.REALIZED_OPEN, why


# --- kind mismatch or unparseable → the distinct cannot-order signal ----------


@pytest.mark.parametrize(
    "period,prior,why",
    [
        ("2026-Q2", "2026-12", "NAMED TRAP: quarter period vs month prior — kind mismatch"),
        ("2026-05", "2026-Q2", "M-vs-Q kind mismatch"),
        ("2026-Q2", "2026-05", "Q-vs-M kind mismatch (reversed)"),
        ("2026-Q10", "2026-Q1", "unparseable period: quarter out of range"),
        ("2026-Q1", "2026-Q10", "unparseable prior: quarter out of range"),
        ("2026-13", "2026-12", "unparseable period: month 13 out of range"),
        ("2026-12", "2026-13", "unparseable prior: month 13 out of range"),
        ("garbage", "2026-Q1", "unparseable period: free text"),
        ("2026-Q1", "garbage", "unparseable prior: free text"),
        ("", "2026-Q1", "blank *period* (prior is set) → cannot order, unlike blank prior"),
    ],
)
def test_kind_mismatch_or_unparseable_is_cannot_order(period, prior, why):
    """AC (critical): a kind mismatch or an unparseable label → `CANNOT_ORDER`.

    No common order exists, so the helper refuses to guess a rung and returns the
    distinct signal — the caller escalates it as `uncategorized_open`, never a
    silent tag. Note the asymmetry with a blank *prior* (which means "no close on
    record" → open): a blank *period* has no key to order, so it escalates. `why`
    documents each case.
    """
    result = derive_certainty(period, prior)
    assert result is CANNOT_ORDER, why
    assert not isinstance(result, Certainty), why  # never a silent grade


# --- the signal is distinct, never a Certainty -------------------------------


def test_cannot_order_signal_is_distinct_and_singular():
    """The cannot-order signal is one identity-comparable value, not a `Certainty`.

    `CANNOT_ORDER` is the sole member of `CannotOrder`, distinct from every ladder
    grade — so a caller can branch on `result is CANNOT_ORDER` and a type checker
    can exhaust the `Certainty | CannotOrder` union.
    """
    assert CANNOT_ORDER is CannotOrder.CANNOT_ORDER
    assert list(CannotOrder) == [CANNOT_ORDER]
    assert not isinstance(CANNOT_ORDER, Certainty)


def test_derive_certainty_is_deterministic():
    """A pure function of its two string inputs — repeat calls agree, nothing mutates."""
    for period, prior in [("2026-Q2", "2026-Q2"), ("2026-Q3", "2026-Q2"), ("2026-Q2", "2026-12")]:
        assert derive_certainty(period, prior) is derive_certainty(period, prior)


# --- public surface ----------------------------------------------------------


def test_certainty_surface_is_exported_from_package():
    """AC: the helper and its signal re-export through `jr_analyst` (the slice convention)."""
    for name in ("derive_certainty", "CannotOrder", "CANNOT_ORDER"):
        assert hasattr(jr_analyst, name), f"{name} not exported from jr_analyst"
        assert name in jr_analyst.__all__, f"{name} missing from __all__"


def test_parse_mirrors_the_bookkeeper_close_guard():
    """The parse agrees with the close guard's on every label — a drift guard.

    This ladder seam must order the identical label set as
    `bookkeeper.skills.close_period._parse_period` (charter: mirror the close
    guard). Asserting behavioural agreement (not regex-string equality) catches
    real drift without breaking on an equivalent pattern rewrite.
    """
    import importlib

    # `import_module` returns the real module from `sys.modules`; a plain
    # `import ... as` would bind the `close_period` *function* the package
    # __init__ re-exports over the submodule name.
    bk = importlib.import_module("bookkeeper.skills.close_period")

    from jr_analyst import certainty as jc

    for label in [
        "2026-Q1", "2026-Q4", "2026-1", "2026-01", "2026-12", " 2026-Q2 ",  # accepted (last: stripped)
        "2026-Q10", "2026-13", "2026-0", "2026-Q0", "x", "", "2026",  # rejected → None
    ]:
        assert jc._parse_period(label) == bk._parse_period(label), label
