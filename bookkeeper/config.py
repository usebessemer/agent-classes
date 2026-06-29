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
from types import MappingProxyType

# Well-known key in `confidence_thresholds` for the attribute→file boundary.
# Its presence is what flips the boundary from inert to live (see §5).
ATTRIBUTION_SKILL = "attribution"

# Well-known key in `confidence_thresholds` for the categorize→propose boundary.
# Its presence is what flips `categorize` from inert (everything surfaced for
# attention) to live (confident matches pre-filled as proposals) — see §5.
CATEGORIZE_SKILL = "categorize"

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
    materiality_floor: float | None = None
    owner_policies: Mapping[str, str] = field(default_factory=dict)
    prior_period_state: str | None = None

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
            materiality_floor=data.get("materiality_floor"),  # type: ignore[arg-type]
            owner_policies=dict(data.get("owner_policies") or {}),  # type: ignore[arg-type]
            prior_period_state=data.get("prior_period_state"),  # type: ignore[arg-type]
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
