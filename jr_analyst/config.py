"""Per-instance config for the jr-analyst — the typed, frozen config surface.

`AnalystConfig` is the jr-analyst's counterpart to `bookkeeper.config.BookkeeperConfig`:
the typed, immutable view of the per-instance fields a deployment binds in its
private instance repo. It mirrors that config's one durable discipline — **fail
fast, reporting every missing required field at once** (`from_mapping`) — so a
misconfigured instance fails clearly at startup rather than deep in a run.

The jr-analyst's config surface is deliberately small. Being a **read-only**
analyst — it proposes graded alignments and writes nothing canonical (see
`ports.py`) — it has no autonomy boundary to arm, so there are no confidence
thresholds to configure the way the Bookkeeper's §5 boundary fields are. The one
genuinely-required field is where a deployment's budget lives
(`budget_source_ref`); the one tunable is which keys an actual is aligned to its
budget on (`align_on`), which defaults to a conservative grain when unset.

This is the **class config surface, not the deploy secrets**: the concrete
`ActualsSource` / `BudgetSource` adapters, their credentials, and the system that
`budget_source_ref` resolves against are adapter/deploy concerns and stay in the
instance repo — this module holds the shape and the fail-fast validation only.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import cast

# The keys `ingest_and_align` matches an actual to its budget on, when an instance
# leaves `align_on` unset. `(account, period)` is the conservative default grain —
# an actual aligns to the budget for the same account in the same period. An
# instance that budgets at a finer grain overrides this; the framework never
# assumes a finer alignment grain than the instance configured.
DEFAULT_ALIGN_ON: tuple[str, ...] = ("account", "period")

# The per-instance fields an instance must declare to be operable. Exactly one in
# v1: a read-only analyst must know where budget comes from; everything else has
# a safe default. Kept as a tuple (mirroring `BookkeeperConfig._REQUIRED`) so the
# fail-fast "report every missing field at once" shape is identical across the two
# class configs even with a single required field today.
_REQUIRED: tuple[str, ...] = ("budget_source_ref",)


class AnalystConfigError(ValueError):
    """Raised when required jr-analyst configuration is missing or blank."""


def _is_blank(value: object) -> bool:
    """A field counts as unset if it is None, a blank string, or an empty collection.

    The same rule as `bookkeeper.config._is_blank`, re-implemented here rather than
    imported so the jr-analyst core stays self-contained: the two packages share a
    distribution, not cross-core imports.
    """
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    if isinstance(value, (list, tuple, set, frozenset, dict)):
        return len(value) == 0
    return False


@dataclass(frozen=True)
class AnalystConfig:
    """Typed, immutable view of the jr-analyst per-instance fields.

    Construct via `from_mapping` (fail-fast validated) or directly. `align_on` is
    frozen to a tuple on construction so the config can be shared across a run
    without a caller mutating it mid-run. Frozen throughout — a run holds this
    config, it never sets it.
    """

    # --- Required: the one field an instance must declare ---
    #: Where this deployment's budget lives — the opaque reference a `BudgetSource`
    #: adapter resolves against (a table name, a sheet id, a path; the framework
    #: never interprets it). The single genuinely-required field: a read-only
    #: analyst that cannot locate its budget has nothing to align actuals against.
    budget_source_ref: str

    # --- Optional: alignment grain (unset → conservative default) ---
    #: The keys `ingest_and_align` matches an actual to its budget on. Defaults to
    #: `DEFAULT_ALIGN_ON` (`("account", "period")`), the conservative grain, when an
    #: instance leaves it unset; not required.
    align_on: tuple[str, ...] = DEFAULT_ALIGN_ON

    def __post_init__(self) -> None:
        # Freeze `align_on` to a tuple on every construction path (direct or
        # `from_mapping`), so a list passed in can't be mutated mid-run and the
        # shared config stays immutable regardless of how it was built.
        object.__setattr__(self, "align_on", tuple(self.align_on))

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> "AnalystConfig":
        """Build a config from an instance's mapping, validated fail-fast.

        Raises `AnalystConfigError` listing **every** missing required field at
        once (mirroring `BookkeeperConfig.from_mapping`), so a misconfigured
        instance gets one clear message rather than failing one field at a time. A
        blank string or empty collection counts as missing (`_is_blank`).
        """
        missing = [name for name in _REQUIRED if _is_blank(data.get(name))]
        if missing:
            raise AnalystConfigError(
                "Missing required Analyst config fields: " + ", ".join(missing)
            )

        # `align_on` is optional: an unset or blank value takes the conservative
        # default rather than aligning on nothing. A supplied value is tuple-ified
        # here (and re-frozen in `__post_init__`). Narrow the untyped mapping value
        # with `cast` rather than a pinned `# type: ignore[code]`: a cast doesn't
        # reclassify across mypy versions the way an overload-sensitive ignore can
        # (see the same choice in `bookkeeper.config.from_mapping`).
        align_on = data.get("align_on")
        return cls(
            budget_source_ref=str(data["budget_source_ref"]),
            align_on=(
                tuple(cast(Iterable[str], align_on))
                if not _is_blank(align_on)
                else DEFAULT_ALIGN_ON
            ),
        )
