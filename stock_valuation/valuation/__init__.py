"""
Valuation strategy package.

Each module here implements a single, independent investment philosophy
behind a common interface (`evaluate(company) -> ModelScore`). Adding a
new model is as simple as creating a new module and registering it in
`scoring.py`.

v2 (growth-hybrid) lineup:
  GrowthModel         — headline growth-at-quality scorer (45%)
  ValueInvestingModel — Buffett-style ROE / debt / margin (15%)
  GARPModel           — Lynch-style PEG check (15%)
  DCFModel            — two-stage discounted cash flow (15%)
  SectorAnalysisModel — structural sector tier overlay (10%)

DeepValueModel is no longer in the default stack but still importable
for backward-compat scripts.
"""

from .value_investing import ValueInvestingModel
from .garp import GARPModel
from .deep_value import DeepValueModel  # legacy; not in DEFAULT_MODELS
from .dcf import DCFModel
from .sector import SectorAnalysisModel
from .growth import GrowthModel

__all__ = [
    "GrowthModel",
    "ValueInvestingModel",
    "GARPModel",
    "DeepValueModel",
    "DCFModel",
    "SectorAnalysisModel",
]
