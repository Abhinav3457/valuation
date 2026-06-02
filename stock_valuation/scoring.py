"""
Scoring engine — v2 (growth-hybrid).

Owns the registry of valuation models and the rules that turn a
collection of per-model scores into a single 0-100 composite plus a
plain-English recommendation.

v2 changes vs the original value-tilted stack:
  * Deep Value (Graham) removed.
  * GrowthModel added as the largest single component (45%).
  * GARP weight halved (now a sanity-check, not a primary signal).
  * Hard filters: companies with weak earnings/quality fundamentals
    are flagged "Filtered" before scoring even matters.
  * Recommendation rules tightened around growth.
"""

from typing import List, Dict, Tuple, Optional

from .models import Company, ModelScore
from .valuation import (
    GrowthModel,
    ValueInvestingModel,
    GARPModel,
    DCFModel,
    SectorAnalysisModel,
)


# (model_instance, weight). Weights MUST sum to 1.0.
#   Growth 45%, Value 15%, GARP 15%, DCF 15%, Sector 10%.
DEFAULT_MODELS = [
    (GrowthModel(),         0.45),
    (ValueInvestingModel(), 0.15),
    (GARPModel(),           0.15),
    (DCFModel(),            0.15),
    (SectorAnalysisModel(), 0.10),
]


# Minimum bar a company must clear to be "scoreable". Stocks failing
# any of these are not investable for a growth-focused mandate.
HARD_FILTERS = {
    "min_profit_cagr": 10.0,    # %
    "min_roe":         12.0,    # %
    "min_revenue_growth": 8.0,  # %
}


def hard_filter_reasons(c: Company) -> List[str]:
    """
    Return a list of failed-filter reasons for `c`, or [] if it passes.

    We use the most informative growth field available per filter:
      * profit CAGR → falls back to growth_rate
      * revenue growth → revenue_cagr → growth_rate
      * roe → as-is
    """
    reasons: List[str] = []

    profit_growth = c.profit_cagr if c.profit_cagr is not None else c.growth_rate
    if profit_growth < HARD_FILTERS["min_profit_cagr"]:
        reasons.append(
            f"Profit CAGR {profit_growth:.1f}% < {HARD_FILTERS['min_profit_cagr']:.0f}%"
        )

    if c.roe < HARD_FILTERS["min_roe"]:
        reasons.append(f"ROE {c.roe:.1f}% < {HARD_FILTERS['min_roe']:.0f}%")

    revenue_growth = c.revenue_cagr if c.revenue_cagr is not None else c.growth_rate
    if revenue_growth < HARD_FILTERS["min_revenue_growth"]:
        reasons.append(
            f"Revenue growth {revenue_growth:.1f}% < {HARD_FILTERS['min_revenue_growth']:.0f}%"
        )

    return reasons


class ScoringEngine:
    def __init__(self, models=None, enforce_hard_filters: bool = True):
        """
        Args:
            models: Optional list of (model, weight). Defaults to DEFAULT_MODELS.
            enforce_hard_filters: When True, companies failing a hard filter
                still get scored, but the recommendation is forced to
                "Filtered" so they're easy to triage. The pandas screener
                additionally drops them before ranking.
        """
        self.models = models if models is not None else DEFAULT_MODELS
        self.enforce_hard_filters = enforce_hard_filters
        total = sum(w for _, w in self.models)
        if abs(total - 1.0) > 1e-6:
            raise ValueError(
                f"Model weights must sum to 1.0, got {total:.4f}"
            )

    def score(self, company: Company) -> Tuple[float, Dict[str, ModelScore]]:
        """
        Run every registered model and return (composite_score, per_model).
        Per-model scores are keyed by display name for easy lookup.
        """
        results: Dict[str, ModelScore] = {}
        composite = 0.0
        for model, weight in self.models:
            ms = model.evaluate(company)
            ms.weight = weight  # authoritative engine weight
            results[ms.name] = ms
            composite += ms.score * weight
        return composite, results

    def recommendation(
        self,
        score: float,
        valuation_gap_pct: float,
        growth_score: Optional[float] = None,
        filtered_reasons: Optional[List[str]] = None,
    ) -> str:
        """
        Map composite score, valuation gap, and growth score to a 5-tier rec.

        Tiers:
          Filtered    -> failed hard filters
          Strong Buy  -> score >= 75 AND growth_score >= 70
          Buy         -> score 60-75
          Hold        -> score 45-60
          Avoid       -> score < 45
        """
        if filtered_reasons:
            return "Filtered"

        if score >= 75 and (growth_score is None or growth_score >= 70):
            return "Strong Buy"
        if score >= 60:
            return "Buy"
        if score >= 45:
            return "Hold"
        return "Avoid"

    @staticmethod
    def quality_flag(
        growth_score: float,
        value_score: float,
        garp_score: float,
        roe: float,
        debt_to_equity: float,
    ) -> Optional[str]:
        """
        Decorate the result with a human-readable badge:
          "High Growth + High Quality" — strong on both axes
          "Growth but Risky"           — high growth, weaker quality/leverage
          (None)                       — low-growth or filtered
        """
        if growth_score < 60:
            return None
        # High quality = clean balance sheet + strong ROE + reasonable price
        if roe >= 18 and debt_to_equity < 1.0 and (value_score >= 55 or garp_score >= 55):
            return "High Growth + High Quality"
        return "Growth but Risky"
