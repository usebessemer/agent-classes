"""The §3 config schema — the class config surface.

`BookkeeperConfig` is the typed, frozen view of the charter §3 per-instance
fields: the state the class *holds* but never *sets*. A deployment binds these
to a specific organization in the private instance repo; nothing here carries a
value.

This is the **class config surface, not the deploy secrets**. API keys, OAuth
tokens, and DB URLs are adapter/deploy concerns and stay in the instance repo —
so this module deliberately does **not** copy instance #1's `.env`/secrets
loader. What it *does* keep is that loader's discipline: **fail fast, reporting
every missing required field at once** (`from_mapping`), so a misconfigured
instance fails clearly at startup rather than deep in a run.

The boundary-governing fields (`confidence_thresholds`, `materiality_floor`) are
optional on purpose: an unset value leaves the §5 boundary *inert* (everything
routes to review) rather than guessing — see `attribution_threshold` and the
orchestrator.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from types import MappingProxyType

# Well-known key in `confidence_thresholds` for the attribute→file boundary.
# Its presence is what flips the boundary from inert to live (see §5).
ATTRIBUTION_SKILL = "attribution"

# Well-known key in `confidence_thresholds` for the categorize→propose boundary.
# Its presence is what flips `categorize` from inert (everything surfaced for
# attention) to live (confident matches pre-filled as proposals) — see §5.
CATEGORIZE_SKILL = "categorize"

# Well-known key in `confidence_thresholds` for the reconcile silent-accept
# boundary. Its presence is what flips `reconcileAccount` from inert (every
# amount+date pair surfaced for confirmation) to live (pairs whose vendor
# similarity clears the floor are accepted as confident matches) — see §5.5.
RECONCILE_VENDOR_SKILL = "reconcile_vendor"

# The ± day window `reconcileAccount` pairs a ledger txn with a statement line
# within, when the date window is left unconfigured. Statements post on a delay,
# so an exact-date requirement would manufacture spurious gaps; ±3 days absorbs
# the usual posting lag. This is a *matching tolerance*, not a §5 autonomy
# boundary (reconcile mutates nothing), so unset takes this concrete default
# rather than going inert.
DEFAULT_RECONCILE_DATE_WINDOW_DAYS = 3

# The conservative vendor-similarity floor an instance should set for the
# reconcile silent-accept boundary (`confidence_thresholds["reconcile_vendor"]`)
# once it has a live feed. After descriptor normalization a genuine same-vendor
# pair scores well above this, so it auto-confirms, while a divergent collision
# scores below and surfaces. A documented recommended value, **not** a silent
# fallback: an unset boundary stays inert (see `reconcile_vendor_threshold`).
# Calibrate against real mangled-descriptor data when a feed exists; a sane
# default suffices for now.
DEFAULT_RECONCILE_VENDOR_FLOOR = 0.7

# The §3 fields an instance must declare to be operable. The boundary-governing
# fields are intentionally absent — unset leaves the boundary inert, not broken.
_REQUIRED: tuple[str, ...] = (
    "chart_of_accounts",
    "accounting_method",
    "jurisdiction",
    "tax_regime",
    "accountant_format",
    "attribution_targets",
    "books_location",
    "intake_channel",
)


class ConfigError(ValueError):
    """Raised when required §3 configuration is missing or blank."""


def _is_blank(value: object) -> bool:
    """A field counts as unset if it is None, a blank string, or an empty collection."""
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    if isinstance(value, (list, tuple, set, frozenset, dict)):
        return len(value) == 0
    return False


def _to_decimal(value: object) -> Decimal | None:
    """Coerce an optional money config value to exact `Decimal` (or `None`).

    `materiality_floor` is compared against `Decimal` transaction amounts in
    `flagAnomaly` (over-materiality), so it must itself be `Decimal`: a
    `Decimal`/`float` comparison raises `TypeError`, and a `float` threshold is
    inexact. Coercion goes through `str()` so a float literal like ``1000.10``
    becomes the exact ``Decimal("1000.10")`` rather than the float's noisy binary
    expansion (`Decimal(1000.10)` would be `1000.0999999…`). `None` (unset) passes
    through unchanged — the §5 inert signal that leaves the over-materiality check
    skipped until the floor is configured.
    """
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


@dataclass(frozen=True)
class BookkeeperConfig:
    """Typed, immutable view of the charter §3 per-instance fields.

    Construct via `from_mapping` (fail-fast validated) or directly. Collections
    are frozen on construction so a config can be shared across a run safely.
    """

    # --- Required: the structural fields an instance must declare ---
    chart_of_accounts: tuple[str, ...]
    accounting_method: str  # e.g. "cash" or "accrual" — drives recognition timing
    jurisdiction: str
    tax_regime: str
    accountant_format: str
    attribution_targets: tuple[str, ...]
    books_location: str
    intake_channel: str

    # --- Optional: boundary + policy fields (unset → inert / conservative) ---
    confidence_thresholds: Mapping[str, float] = field(default_factory=dict)
    # `Decimal | None`, not `float`: the over-materiality boundary (`flagAnomaly`,
    # §5.6) compares it against `Decimal` amounts, so it is coerced to exact
    # `Decimal` on construction (`_to_decimal`). `None` = unset = inert (the
    # over-materiality check is skipped until configured).
    materiality_floor: Decimal | None = None
    owner_policies: Mapping[str, str] = field(default_factory=dict)
    prior_period_state: str | None = None
    # A matching tolerance, not a boundary field: unset → a concrete default
    # (`DEFAULT_RECONCILE_DATE_WINDOW_DAYS`), never inert (see the accessor).
    reconcile_date_window_days: int | None = None

    def __post_init__(self) -> None:
        # Freeze collections so the shared config can't be mutated mid-run.
        object.__setattr__(self, "chart_of_accounts", tuple(self.chart_of_accounts))
        object.__setattr__(self, "attribution_targets", tuple(self.attribution_targets))
        object.__setattr__(
            self,
            "confidence_thresholds",
            MappingProxyType(dict(self.confidence_thresholds)),
        )
        object.__setattr__(
            self, "owner_policies", MappingProxyType(dict(self.owner_policies))
        )
        # Coerce the materiality floor to exact `Decimal` on every construction
        # path (direct or `from_mapping`), so a `float`/`int`/`str` passed in can
        # never reach `flagAnomaly`'s `Decimal` amount comparison as a raw `float`.
        object.__setattr__(self, "materiality_floor", _to_decimal(self.materiality_floor))

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> "BookkeeperConfig":
        """Build a config from an instance's §3 mapping, validated fail-fast.

        Raises `ConfigError` listing **every** missing required field at once,
        so a misconfigured instance gets one clear message rather than failing
        one field at a time.
        """
        missing = [name for name in _REQUIRED if _is_blank(data.get(name))]
        if missing:
            raise ConfigError(
                "Missing required Bookkeeper config fields (charter §3): "
                + ", ".join(missing)
            )

        return cls(
            chart_of_accounts=tuple(data["chart_of_accounts"]),  # type: ignore[arg-type]
            accounting_method=str(data["accounting_method"]),
            jurisdiction=str(data["jurisdiction"]),
            tax_regime=str(data["tax_regime"]),
            accountant_format=str(data["accountant_format"]),
            attribution_targets=tuple(data["attribution_targets"]),  # type: ignore[arg-type]
            books_location=str(data["books_location"]),
            intake_channel=str(data["intake_channel"]),
            confidence_thresholds=dict(data.get("confidence_thresholds") or {}),  # type: ignore[arg-type]
            materiality_floor=_to_decimal(data.get("materiality_floor")),
            owner_policies=dict(data.get("owner_policies") or {}),  # type: ignore[arg-type]
            prior_period_state=data.get("prior_period_state"),  # type: ignore[arg-type]
            reconcile_date_window_days=data.get("reconcile_date_window_days"),  # type: ignore[arg-type]
        )

    def attribution_threshold(self) -> float | None:
        """The attribute→file confidence cutoff, or `None` when unset.

        `None` is the §5 *inert* signal: the orchestrator routes every item to
        review instead of auto-filing, so no instance goes live silently
        auto-filing before its boundary is configured.
        """
        return self.confidence_thresholds.get(ATTRIBUTION_SKILL)

    def categorize_threshold(self) -> float | None:
        """The categorize→propose confidence cutoff, or `None` when unset.

        `None` is the §5 *inert* signal for `categorize` (mirrors
        `attribution_threshold`): the skill surfaces every transaction for human
        attention rather than pre-filling any as a confident proposal, so no
        instance goes live with auto-pre-filled categories before its boundary
        is configured.
        """
        return self.confidence_thresholds.get(CATEGORIZE_SKILL)

    def reconcile_date_window(self) -> int:
        """The ± day window `reconcileAccount` pairs a ledger txn with a statement line.

        Unlike the confidence thresholds, this is a **matching tolerance, not a
        §5 autonomy boundary** — reconcile is detection-only and mutates nothing,
        so there is no "go live" to gate. An unset value therefore takes a
        concrete default (`DEFAULT_RECONCILE_DATE_WINDOW_DAYS`, ±3 days) rather
        than the inert `None` the boundary fields use. Widening the window only
        pairs more lines (fewer date-driven gaps); it never suppresses a real gap.
        """
        window = self.reconcile_date_window_days
        return DEFAULT_RECONCILE_DATE_WINDOW_DAYS if window is None else window

    def reconcile_vendor_threshold(self) -> float | None:
        """The reconcile silent-accept vendor floor, or `None` when unset.

        `None` is the §5 *inert* signal for `reconcileAccount` (mirrors
        `attribution_threshold` / `categorize_threshold`): with no floor set the
        skill leans toward surfacing — every amount+date pair is routed to
        `to_confirm` for a human, and **nothing** is silently accepted as a
        confident match — so no instance auto-confirms a divergent-vendor
        collision before its boundary is configured. When set, a linked pair whose
        normalized vendor similarity clears the floor becomes a confident match;
        below it, the pair is surfaced for confirm/reject. The conservative value
        to configure is `DEFAULT_RECONCILE_VENDOR_FLOOR`.
        """
        return self.confidence_thresholds.get(RECONCILE_VENDOR_SKILL)
