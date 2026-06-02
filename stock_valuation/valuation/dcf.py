"""
Discounted Cash Flow (DCF) model.

Estimates intrinsic value per share by projecting future free cash
flows and discounting them back to today. We use a two-stage model:

  Stage 1: explicit growth for `projection_years` years using the
           company's expected `growth_rate` (capped to keep results
           sane — analysts notoriously over-extrapolate).
  Stage 2: a terminal value computed via the Gordon growth formula
           with a much lower perpetual growth rate.

If free cash flow is missing we fall back to net profit per share —
imperfect, but better than refusing to value the company at all.
"""

from ..models import Company, ModelScore


class DCFModel:
    name = "DCF"

    # Tuned for the Indian market — risk-free rate ~7%, equity risk
    # premium ~5%. Override per-sector if needed.
    DISCOUNT_RATE = 0.12
    TERMINAL_GROWTH = 0.04
    PROJECTION_YEARS = 10
    MAX_GROWTH_CAP = 0.25  # 25% — refuse to extrapolate higher

    def evaluate(self, c: Company) -> ModelScore:
        intrinsic = self.intrinsic_value(c)
        if intrinsic <= 0:
            return ModelScore(
                self.name, 0.0, weight=0.25,
                notes="Insufficient cash-flow data for DCF",
            )

        # Margin of safety = how much cheaper the market price is
        # versus our estimate. >50% is exceptional, >25% is solid.
        margin = (intrinsic - c.current_price) / intrinsic
        if margin >= 0.5:
            score = 100.0
        elif margin >= 0.25:
            score = 75 + (margin - 0.25) * 100
        elif margin >= 0:
            score = 50 + margin * 100
        elif margin >= -0.25:
            score = 50 + margin * 200  # 0 -> 50, -0.25 -> 0
        else:
            score = 0.0

        score = max(0.0, min(100.0, score))
        notes = (
            f"Intrinsic Rs {intrinsic:.2f} vs Price Rs {c.current_price:.2f} "
            f"({margin * 100:+.1f}% margin)"
        )
        return ModelScore(self.name, score, weight=0.25, notes=notes)

    def intrinsic_value(self, c: Company) -> float:
        """
        Per-share intrinsic value. Public so the analyzer can also use
        the same number for the headline "Intrinsic Value" output.
        """
        cash_per_share = self._cash_flow_per_share(c)
        if cash_per_share <= 0:
            return 0.0

        # Cap growth — never trust >25% perpetual growth.
        g = min(max(c.growth_rate / 100.0, 0.0), self.MAX_GROWTH_CAP)
        r = self.DISCOUNT_RATE
        tg = self.TERMINAL_GROWTH

        pv = 0.0
        cf = cash_per_share
        for year in range(1, self.PROJECTION_YEARS + 1):
            cf *= (1 + g)
            pv += cf / ((1 + r) ** year)

        # Terminal value via Gordon growth, then discount it back.
        terminal_cf = cf * (1 + tg)
        terminal_value = terminal_cf / (r - tg)
        pv += terminal_value / ((1 + r) ** self.PROJECTION_YEARS)
        return pv

    def intrinsic_range(self, c: Company) -> tuple:
        """
        Return a low/high band by perturbing the discount rate by
        ±2 percentage points. Gives users an honest sense of the
        sensitivity of the model.
        """
        base_r = self.DISCOUNT_RATE
        try:
            self.DISCOUNT_RATE = base_r + 0.02
            low = self.intrinsic_value(c)
            self.DISCOUNT_RATE = base_r - 0.02
            high = self.intrinsic_value(c)
        finally:
            self.DISCOUNT_RATE = base_r
        return low, high

    @staticmethod
    def _cash_flow_per_share(c: Company) -> float:
        """
        Use FCF per share if provided, otherwise approximate with EPS.
        EPS overstates true distributable cash but is a reasonable
        proxy for capital-light businesses.
        """
        if c.cash_flow is not None and c.cash_flow > 0:
            return c.cash_flow
        if c.shares_outstanding and c.shares_outstanding > 0:
            return c.profit / c.shares_outstanding
        # Last resort: derive EPS from PE & price.
        if c.pe_ratio > 0:
            return c.current_price / c.pe_ratio
        return 0.0
