"""
Sector Analysis model.

Uses the tiered SECTOR_GROWTH_TIERS mapping from models.py to score
each sector on a 0-10 structural-growth scale, then expands to 0-100.

The tiering captures India-specific dynamics:
  Tier 1: NBFC / fintech / digital / SaaS         (9-10)
  Tier 2: IT services / pharma / specialty chem   (7-8)
  Tier 3: FMCG / financial services               (6-7)
  Tier 4: industrials / autos / cyclicals         (4-5)
  Tier 5: PSU / oil / metals / utilities          (3-4)
"""

from ..models import (
    Company,
    ModelScore,
    sector_growth_score_0_10,
    GROWING_SECTORS,
    DECLINING_SECTORS,
)


class SectorAnalysisModel:
    name = "Sector Strength"

    def evaluate(self, c: Company) -> ModelScore:
        if not c.sector:
            return ModelScore(
                self.name, 50.0, weight=0.10,
                notes="Sector not provided — neutral score",
            )

        sector = c.sector.strip()

        # Backward compat: explicit override lists still take precedence.
        if sector in GROWING_SECTORS:
            return ModelScore(
                self.name, 90.0, weight=0.10,
                notes=f"{sector}: structurally growing sector",
            )
        if sector in DECLINING_SECTORS:
            return ModelScore(
                self.name, 20.0, weight=0.10,
                notes=f"{sector}: structurally declining sector",
            )

        # Tiered substring match — handles Yahoo Finance labels like
        # "Financial Services", "Communication Services", etc.
        tier_score = sector_growth_score_0_10(sector)  # 0-10
        score = tier_score * 10.0
        if tier_score >= 8.5:
            band = "high-growth tier"
        elif tier_score >= 6.5:
            band = "structural compounder"
        elif tier_score >= 5.5:
            band = "stable / mature"
        elif tier_score >= 4.0:
            band = "cyclical"
        else:
            band = "low-growth tier"

        return ModelScore(
            self.name, score, weight=0.10,
            notes=f"{sector}: {band} (tier {tier_score:.1f}/10)",
        )
