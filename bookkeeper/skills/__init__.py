"""The Bookkeeper skills — the charter §4 computation skills, one module each.

A skill is a single framework operation built on the agnostic core (ports +
model + config). Skills compute over what the pipeline stored; they keep the
regime / jurisdiction rules in the framework, off the adapter. Each is
vertical-agnostic and adapter-free: it drives ports, never a concrete system.

Built here:

- `track_tax` — break out + total reclaimable tax per attribution target and
  period (charter `trackTax`). Returns a *proposed* `TaxSummary`; per §5.4 it
  writes nothing canonical. Tax regime selected by `config.tax_regime`.
"""

from bookkeeper.skills.track_tax import (
    HstRegime,
    TargetTax,
    TaxFlag,
    TaxLine,
    TaxRegime,
    TaxSummary,
    UnknownTaxRegime,
    select_regime,
    track_tax,
)

__all__ = [
    "track_tax",
    "TaxSummary",
    "TargetTax",
    "TaxFlag",
    "TaxLine",
    "TaxRegime",
    "HstRegime",
    "select_regime",
    "UnknownTaxRegime",
]
