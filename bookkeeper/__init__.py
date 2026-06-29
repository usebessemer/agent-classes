"""The Bookkeeper agent-class framework — the adapter-agnostic core.

The reusable, vertical-agnostic implementation of the Bookkeeper charter: the
skill ports, the Standing-executor orchestrator (with the §5 fail-safe), the §3
config schema, and the Contract A/B interfaces. A deployment imports this
package, implements the ports as adapters in its private instance repo, binds
the §3 fields to its organization, and runs.

Nothing here is client- or system-specific. Adapters and instance config live
in the private instance repo — never in this package.
"""

from bookkeeper.config import ATTRIBUTION_SKILL, BookkeeperConfig, ConfigError
from bookkeeper.contracts import (
    Notifier,
    PackageWriter,
    ReviewQueue,
    RunLog,
    RunLogEntry,
    RunOutcome,
)
from bookkeeper.model import ExtractedTransaction, IntakeItem, Transaction
from bookkeeper.orchestrator import StandingRun
from bookkeeper.ports import (
    AttributionResolver,
    Extractor,
    IntakeSource,
    LedgerSink,
    LedgerSource,
)
from bookkeeper.skills import (
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

__version__ = "0.1.0"

__all__ = [
    "__version__",
    # data model
    "IntakeItem",
    "ExtractedTransaction",
    "Transaction",
    # skill ports
    "IntakeSource",
    "Extractor",
    "AttributionResolver",
    "LedgerSink",
    "LedgerSource",
    # contracts A/B
    "PackageWriter",
    "ReviewQueue",
    "RunLog",
    "RunLogEntry",
    "RunOutcome",
    "Notifier",
    # config (§3)
    "BookkeeperConfig",
    "ConfigError",
    "ATTRIBUTION_SKILL",
    # orchestrator (§5 spine)
    "StandingRun",
    # skills (§4 computation)
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
