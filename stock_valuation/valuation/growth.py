"""
Growth Model — the headline component of the v2 hybrid scoring engine.

Scores companies on their ability to *compound* future cash flows, not on
how cheap they look today. Built to combat the value-trap bias of the
older Buffett+Graham+DCF stack which over-selects PSU banks, cyclicals,
and structurally challenged businesses simply because they're cheap.

Score (0-100) is a weighted blend of six normalized inputs:

    Growth Score =
        0.30 * Revenue CAGR     (3-5y)
      + 0.30 * Profit CAGR      (3-5y, must keep up with revenue for full credit)
      + 0.15 * ROE
      + 0.10 * Reinvestment Rate
      + 0.10 * Sector Growth Score
      + 0.05 * Operating Leverage   (profit growth / revenue growth)

Missing inputs degrade gracefully — each sub-score has an explicit
fallback so a partial dataset still produces a comparable score.
"""

from __future__ import annotations

from typing import Optional

from ..models import Company, ModelScore, sector_growth_score_0_10


# ---------------------------------------------------------------------------
# Normalization helpers — each maps a raw metric to a 0-100 sub-score.
# Anchor points are deliberate, calibrated to public-equity reality on
# Indian large/mid-caps. Linear interpolation between anchors keeps the
# scoring monotonic and free of cliffs.
# ---------------------------------------------------------------------------
def _interp(x: float, points: list[tuple[float, float]]) -> float:
    """Piecewise-linear interpolation. `points` must be sorted by x."""
    if x <= points[0][0]:
        return points[0][1]
    if x >= points[-1][0]:
        return points[-1][1]
    for (x1, y1), (x2, y2) in zip(points, points[1:]):
        if x1 <= x <= x2:
            t = (x - x1) / (x2 - x1) if x2 != x1 else 0.0
            return y1 + (y2 - y1) * t
    return points[-1][1]


def normalize_revenue_cagr(cagr_pct: float) -> float:
    """Revenue CAGR % -> 0-100. Anchored at 0%/0, 8%/40, 15%/70, 25%/100."""
    return _interp(cagr_pct, [(-20, 0), (0, 0), (8, 40), (15, 70), (25, 100), (50, 100)])


def normalize_profit_cagr(profit_cagr_pct: float, revenue_cagr_pct: float) -> float:
    """
    Profit CAGR % -> 0-100, with a margin-leverage modifier.

    Spec: profit CAGR must be >= revenue CAGR for full credit. We grade
    the raw profit CAGR on the same scale as revenue, then apply a
    multiplier:
      profit_cagr >= revenue_cagr     -> 1.0   (full credit)
      profit_cagr ~ revenue_cagr      -> ~0.85 (margin compression OK if mild)
      profit_cagr << revenue_cagr     -> 0.6   (real margin erosion)
    """
    base = _interp(profit_cagr_pct, [(-20, 0), (0, 0), (8, 35), (15, 65), (25, 100), (50, 100)])
    if revenue_cagr_pct <= 0:
        return base
    ratio = profit_cagr_pct / revenue_cagr_pct
    if ratio >= 1.0:
        modifier = 1.0
    elif ratio >= 0.8:
        modifier = 0.9
    elif ratio >= 0.5:
        modifier = 0.75
    else:
        modifier = 0.6
    return base * modifier


def normalize_roe(roe_pct: float) -> float:
    """ROE % -> 0-100. Anchored at <10%/low, 15-20%/good, >20%/excellent."""
    return _interp(roe_pct, [(0, 0), (8, 20), (10, 35), (15, 65), (20, 85), (30, 100), (50, 100)])


def normalize_reinvestment(rate: float) -> float:
    """
    Reinvestment rate (0-1+ ratio of capex/OCF) -> 0-100.

    Higher is generally better for compounders, but extreme values
    (ratio > 1 = burning cash) are penalized as financially fragile.
    """
    if rate <= 0:
        return 10.0  # paying everything out = no compounding
    return _interp(rate, [(0, 10), (0.2, 35), (0.4, 60), (0.6, 80), (0.8, 95), (1.0, 100), (1.5, 50), (2.0, 20)])


def normalize_sector(sector: str | None) -> float:
    """0-10 tier score -> 0-100 contribution."""
    return sector_growth_score_0_10(sector) * 10.0


def normalize_operating_leverage(profit_growth: float, revenue_growth: float) -> float:
    """
    Operating leverage = profit_growth / revenue_growth.

    > 1.2 = strong (margins expanding) -> high score
    ~ 1.0 = neutral
    < 0.8 = margin compression          -> low score
    """
    if revenue_growth <= 0 or profit_growth is None:
        return 50.0  # neutral when undefined (loss-making or shrinking)
    leverage = profit_growth / revenue_growth
    return _interp(leverage, [(0.0, 10), (0.5, 30), (0.8, 50), (1.0, 65), (1.2, 85), (1.5, 100), (3.0, 100)])


# ---------------------------------------------------------------------------
# Public model
# ---------------------------------------------------------------------------
class GrowthModel:
    """
    The headline 0-100 growth score. Designed to be paired with other
    models in `ScoringEngine` rather than used standalone.
    """

    name = "Growth"

    # Sub-component weights inside the growth score itself (sum = 1.0).
    SUB_WEIGHTS = {
        "revenue_cagr":      0.30,
        "profit_cagr":       0.30,
        "roe":               0.15,
        "reinvestment":      0.10,
        "sector":            0.10,
        "operating_leverage": 0.05,
    }

    def evaluate(self, c: Company) -> ModelScore:
        # Pull or fall back. `growth_rate` on Company is a single-period
        # growth figure; it's the best proxy when CAGR isn't supplied.
        rev_cagr = self._first_non_null(c.revenue_cagr, c.growth_rate, 0.0)
        prof_cagr = self._first_non_null(c.profit_cagr, c.growth_rate, 0.0)
        reinvestment = self._derive_reinvestment_rate(c)

        # Component scores
        s_rev = normalize_revenue_cagr(rev_cagr)
        s_prof = normalize_profit_cagr(prof_cagr, rev_cagr)
        s_roe = normalize_roe(c.roe)
        s_reinv = normalize_reinvestment(reinvestment)
        s_sector = normalize_sector(c.sector)
        s_oplev = normalize_operating_leverage(prof_cagr, rev_cagr)

        w = self.SUB_WEIGHTS
        score = (
            w["revenue_cagr"] * s_rev
            + w["profit_cagr"] * s_prof
            + w["roe"] * s_roe
            + w["reinvestment"] * s_reinv
            + w["sector"] * s_sector
            + w["operating_leverage"] * s_oplev
        )
        score = max(0.0, min(100.0, score))

        notes = (
            f"Rev CAGR {rev_cagr:.1f}% (s={s_rev:.0f}); "
            f"Profit CAGR {prof_cagr:.1f}% (s={s_prof:.0f}); "
            f"ROE {c.roe:.1f}% (s={s_roe:.0f}); "
            f"Reinv {reinvestment:.2f} (s={s_reinv:.0f}); "
            f"OpLev {(prof_cagr / rev_cagr):.2f} (s={s_oplev:.0f})"
            if rev_cagr > 0 else
            f"Rev CAGR {rev_cagr:.1f}% (s={s_rev:.0f}); "
            f"Profit CAGR {prof_cagr:.1f}% (s={s_prof:.0f}); "
            f"ROE {c.roe:.1f}% (s={s_roe:.0f})"
        )
        return ModelScore(self.name, score, weight=0.45, notes=notes)

    # ---- helpers ------------------------------------------------------
    @staticmethod
    def _first_non_null(*candidates) -> float:
        for v in candidates:
            if v is not None:
                return float(v)
        return 0.0

    @staticmethod
    def _derive_reinvestment_rate(c: Company) -> float:
        """
        Reinvestment rate = (capex or asset growth) / OCF.

        We rarely get capex in the dashboard's input, so we proxy with
        `1 - payout_ratio`. The intuition: dividend-stingy companies
        plowed earnings back, which is what the metric is trying to
        capture in the first place.
        """
        if c.reinvestment_rate is not None:
            return float(c.reinvestment_rate)
        if c.payout_ratio is not None:
            # Cap to [0,1] — Yahoo sometimes reports payout > 1 (special divs).
            return max(0.0, min(1.0, 1.0 - float(c.payout_ratio)))
        # Last resort: high ROE + decent growth => reinvesting heavily
        if c.roe >= 15 and c.growth_rate >= 10:
            return 0.6
        return 0.3
