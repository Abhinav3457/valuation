"""
Value Investing model — Warren Buffett style.

Looks for "wonderful businesses at fair prices":
  * High Return on Equity (>15%) — capital is being put to good use.
  * Consistent profit growth (>10%) — earnings power is durable.
  * Low debt — survives downturns without diluting equity.

The score is the simple average of three sub-scores so that a stock
which excels in two areas but is mediocre in the third still rates
well, mirroring how Buffett tolerates trade-offs in real life.
"""

from ..models import Company, ModelScore


class ValueInvestingModel:
    name = "Value Investing"

    # Buffett's well-known thresholds. Pulled out as class attributes
    # so they can be overridden in tests or specialized subclasses.
    ROE_TARGET = 15.0          # percent
    GROWTH_TARGET = 10.0       # percent
    MAX_DEBT_EQUITY = 0.5      # ratio

    def evaluate(self, c: Company) -> ModelScore:
        # Each sub-metric is normalized to 0-100. We cap at 100 so a
        # spectacular ROE of 60% doesn't drown out other dimensions.
        roe_score = min(c.roe / self.ROE_TARGET, 2.0) * 50
        growth_score = min(c.growth_rate / self.GROWTH_TARGET, 2.0) * 50

        # Debt score is inverted: less debt = higher score. A D/E above
        # 1.0 brings it to zero — Buffett broadly avoids leveraged
        # businesses outside of regulated financials.
        de = c.debt_to_equity
        if de <= self.MAX_DEBT_EQUITY:
            debt_score = 100.0
        elif de >= 1.0:
            debt_score = 0.0
        else:
            # Linear ramp between 0.5 and 1.0
            debt_score = (1.0 - de) / (1.0 - self.MAX_DEBT_EQUITY) * 100

        score = (roe_score + growth_score + debt_score) / 3
        score = max(0.0, min(100.0, score))

        notes = (
            f"ROE {c.roe:.1f}% (target >{self.ROE_TARGET}%), "
            f"Growth {c.growth_rate:.1f}% (target >{self.GROWTH_TARGET}%), "
            f"D/E {de:.2f} (target <{self.MAX_DEBT_EQUITY})"
        )
        return ModelScore(self.name, score, weight=0.25, notes=notes)
