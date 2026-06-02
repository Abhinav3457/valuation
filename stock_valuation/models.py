"""
Data models for the stock valuation system.

Defines the input shape (Company) that every valuation model consumes
and the output shape (ValuationResult) that the analyzer returns.
Keeping these as dataclasses gives us validation, defaults, and
serialization for free.
"""

from dataclasses import dataclass, field, asdict
from typing import Optional, Dict, Any


# Sectors that are commonly considered structurally growing in India.
# Used by the SectorAnalysis model. Easy to extend.
GROWING_SECTORS = {
    "IT", "Information Technology", "Technology",
    "Pharma", "Pharmaceuticals", "Healthcare",
    "FMCG", "Consumer Goods",
    "Renewable Energy", "Green Energy",
    "EV", "Electric Vehicles",
    "Financial Services", "Private Banking",
    "Specialty Chemicals",
    "Defence", "Defense",
}

DECLINING_SECTORS = {
    "Print Media", "Tobacco", "Coal",
    "Landline Telecom", "Traditional Retail",
}


# Tiered sector growth scores on a 0-10 scale (used by GrowthModel and
# the upgraded SectorAnalysisModel). Captures structural tailwinds.
# Yahoo Finance / Indian sector names are matched case-insensitively
# via substrings to keep the mapping resilient to label drift.
SECTOR_GROWTH_TIERS = {
    # Tier 1 (9-10): structural compounders, digital adoption, fintech
    "nbfc": 9.5, "fintech": 10.0, "digital": 9.5, "internet": 9.0,
    "ecommerce": 9.0, "saas": 9.5, "platform": 9.0,
    # Tier 2 (7-8): IT services / midcap tech, specialty chemicals,
    # consumer durables, healthcare
    "technology": 7.5, "it ": 7.5, "software": 8.0, "communication": 7.0,
    "specialty chemical": 7.5, "healthcare": 7.5, "pharma": 7.0,
    "consumer durables": 7.0, "renewable": 8.5, "ev": 8.5,
    # Tier 3 (6-7): FMCG, financial services
    "fmcg": 6.5, "consumer goods": 6.5, "consumer defensive": 6.0,
    "financial services": 6.5, "private bank": 6.5,
    # Tier 4 (4-5): industrials, capital goods, real estate, auto
    "industrial": 4.5, "capital goods": 5.0, "real estate": 4.5,
    "automobile": 5.0, "auto": 4.5, "consumer cyclical": 4.5,
    # Tier 5 (3-5): PSU / oil / metals / utilities — slow growers
    "psu": 3.5, "oil": 3.5, "energy": 3.5, "gas": 4.0,
    "metal": 3.5, "mining": 3.0, "basic material": 3.5,
    "utilities": 4.0, "power": 4.0,
}


def sector_growth_score_0_10(sector: str | None) -> float:
    """Return a 0-10 sector-growth score using substring matching."""
    if not sector:
        return 5.0  # neutral
    s = sector.lower()
    # Prefer the most specific (longest) matching key.
    for key in sorted(SECTOR_GROWTH_TIERS.keys(), key=len, reverse=True):
        if key in s:
            return SECTOR_GROWTH_TIERS[key]
    return 5.0


@dataclass
class Company:
    """
    Represents a single listed company and the financials needed to
    value it. All monetary fields should be in the same unit (e.g.
    INR Crores) — the models only use ratios so the unit cancels out.

    Required fields are minimal so partial data sets still work; the
    valuation models gracefully degrade when optional inputs are
    missing.
    """

    name: str                     # Display name, e.g. "Infosys"
    symbol: str                   # NSE/BSE ticker, e.g. "INFY"

    # Core financials (latest annual)
    revenue: float                # Total revenue / sales
    profit: float                 # Net profit (PAT)
    debt: float                   # Total debt
    equity: float                 # Shareholders' equity / book value

    # Market & ratio data
    pe_ratio: float               # Price to Earnings
    pb_ratio: float               # Price to Book
    roe: float                    # Return on Equity, in percent (e.g. 18.5)
    growth_rate: float            # Expected/historical earnings growth %
    current_price: float          # Latest market price per share

    # Optional inputs — improve accuracy when present
    cash_flow: Optional[float] = None      # Free cash flow per share
    sector: Optional[str] = None           # E.g. "IT", "Pharma"
    shares_outstanding: Optional[float] = None

    # ---- Growth-model inputs (optional; fall back to growth_rate) ----
    revenue_cagr: Optional[float] = None      # 3-5y revenue CAGR, percent
    profit_cagr: Optional[float] = None       # 3-5y net-profit CAGR, percent
    payout_ratio: Optional[float] = None      # Dividend payout, fraction (0-1)
    reinvestment_rate: Optional[float] = None # Capex / OCF, fraction (0-1)
    operating_cashflow: Optional[float] = None  # Absolute OCF, optional

    def __post_init__(self) -> None:
        # Cheap sanity checks at the boundary. We do not silently
        # coerce — if a caller passes garbage we want a loud failure.
        if self.current_price <= 0:
            raise ValueError(f"{self.symbol}: current_price must be > 0")
        if self.equity <= 0:
            raise ValueError(f"{self.symbol}: equity must be > 0")

    @property
    def debt_to_equity(self) -> float:
        """Standard leverage ratio used by the value-investing model."""
        return self.debt / self.equity if self.equity else float("inf")

    @property
    def peg_ratio(self) -> float:
        """
        Lynch's PEG = PE / growth%. A PEG below 1 historically signals
        a stock that is cheap relative to its growth. Returns infinity
        for non-growing companies so the GARP model marks them down.
        """
        if self.growth_rate <= 0:
            return float("inf")
        return self.pe_ratio / self.growth_rate


@dataclass
class ModelScore:
    """Per-strategy score plus a short human explanation."""
    name: str
    score: float                  # Normalized 0-100
    weight: float                 # Weight in the final composite
    notes: str = ""


@dataclass
class ValuationResult:
    """The final, user-facing valuation report for a company."""
    company: Company
    intrinsic_value: float
    intrinsic_value_low: float
    intrinsic_value_high: float
    current_price: float
    valuation_gap_pct: float      # +ve = undervalued, -ve = overvalued
    final_score: float            # 0-100 composite
    recommendation: str           # Strong Buy / Buy / Hold / Avoid / Filtered
    model_scores: Dict[str, ModelScore] = field(default_factory=dict)
    # Growth-mode extras — populated by the v2 hybrid scoring engine
    flag: Optional[str] = None             # e.g. "High Growth + High Quality"
    filtered_reasons: list = field(default_factory=list)  # e.g. ["Profit CAGR < 10%"]

    def to_dict(self) -> Dict[str, Any]:
        """JSON-serializable view, used by CLI and tests."""
        d = {
            "name": self.company.name,
            "symbol": self.company.symbol,
            "sector": self.company.sector,
            "intrinsic_value": round(self.intrinsic_value, 2),
            "intrinsic_value_range": [
                round(self.intrinsic_value_low, 2),
                round(self.intrinsic_value_high, 2),
            ],
            "current_price": round(self.current_price, 2),
            "valuation_gap_pct": round(self.valuation_gap_pct, 2),
            "final_score": round(self.final_score, 2),
            "recommendation": self.recommendation,
            "model_scores": {
                k: {
                    "score": round(v.score, 2),
                    "weight": v.weight,
                    "notes": v.notes,
                }
                for k, v in self.model_scores.items()
            },
            "flag": self.flag,
            "filtered_reasons": list(self.filtered_reasons),
        }
        return d
