"""
StockAnalyzer — the orchestrator the rest of the app talks to.

Responsibilities:
  * Run all valuation models against a single Company.
  * Use the DCF model as the source of the headline intrinsic value
    (with a low/high band for honesty about uncertainty).
  * Compute the valuation gap and turn the composite score into a
    Buy/Hold/Avoid recommendation.
  * Provide convenience methods for batch analysis, sorting, and
    side-by-side comparison.
"""

import logging
from typing import Iterable, List, Dict

from .models import Company, ValuationResult
from .scoring import ScoringEngine, hard_filter_reasons
from .valuation.dcf import DCFModel

log = logging.getLogger("stock_valuation.analyzer")


class StockAnalyzer:
    def __init__(self, scoring_engine: ScoringEngine = None):
        self.engine = scoring_engine or ScoringEngine()
        # We hold a reference to the DCF model specifically because
        # the analyzer also asks it for an intrinsic value range — a
        # responsibility no other model has.
        self._dcf = DCFModel()

    # ------------------------------------------------------------------
    # Single-company analysis
    # ------------------------------------------------------------------
    def analyze(self, company: Company) -> ValuationResult:
        composite, model_scores = self.engine.score(company)

        intrinsic = self._dcf.intrinsic_value(company)
        low, high = self._dcf.intrinsic_range(company)

        # Valuation gap: positive => undervalued. We use the headline
        # intrinsic value (not the range mid-point) so that the number
        # always matches what users see in the report.
        if intrinsic > 0:
            gap_pct = (intrinsic - company.current_price) / intrinsic * 100
        else:
            gap_pct = 0.0

        # Hard filters (growth-mode quality gate). When a company fails
        # these, recommendation gets forced to "Filtered" so it stands
        # out clearly in the UI without distorting the underlying score.
        filtered = (
            hard_filter_reasons(company)
            if self.engine.enforce_hard_filters else []
        )

        growth_score = (
            model_scores["Growth"].score if "Growth" in model_scores else None
        )
        recommendation = self.engine.recommendation(
            composite, gap_pct, growth_score=growth_score, filtered_reasons=filtered
        )

        # Quality / risk flag
        flag = None
        if growth_score is not None:
            value_score = model_scores.get("Value Investing")
            garp_score = model_scores.get("GARP (Lynch)")
            flag = self.engine.quality_flag(
                growth_score=growth_score,
                value_score=value_score.score if value_score else 0,
                garp_score=garp_score.score if garp_score else 0,
                roe=company.roe,
                debt_to_equity=company.debt_to_equity,
            )

        return ValuationResult(
            company=company,
            intrinsic_value=intrinsic,
            intrinsic_value_low=low,
            intrinsic_value_high=high,
            current_price=company.current_price,
            valuation_gap_pct=gap_pct,
            final_score=composite,
            recommendation=recommendation,
            model_scores=model_scores,
            flag=flag,
            filtered_reasons=filtered,
        )

    # ------------------------------------------------------------------
    # Batch helpers
    # ------------------------------------------------------------------
    def analyze_many(self, companies: Iterable[Company]) -> List[ValuationResult]:
        """
        Analyze a list of companies. Order is preserved. Per-company
        failures are logged and skipped so one bad record can't sink
        the whole batch — critical for large fetches where a single
        edge-case company (e.g. zero equity, missing required fields)
        would otherwise return a 500 to the dashboard.
        """
        results = []
        for c in companies:
            try:
                results.append(self.analyze(c))
            except Exception as e:
                log.warning("analyze failed for %s: %s", getattr(c, "symbol", "?"), e)
        return results

    def top_undervalued(
        self, companies: Iterable[Company], n: int = 5
    ) -> List[ValuationResult]:
        """
        Return the top-N companies ranked by composite score, filtered
        to those that are at least fairly valued (gap >= 0). Sorting
        on score AND keeping only undervalued names matches what a
        real investor would want to act on.
        """
        results = self.analyze_many(companies)
        results = [r for r in results if r.valuation_gap_pct >= 0]
        results.sort(key=lambda r: (r.final_score, r.valuation_gap_pct), reverse=True)
        return results[:n]

    def top_growth(
        self,
        companies: Iterable[Company],
        n: int = 10,
        require_pass_filters: bool = True,
    ) -> List[ValuationResult]:
        """
        Top-N companies ranked by Growth sub-score (not composite).

        With `require_pass_filters=True` (the default), companies that
        fail the hard filters are excluded — this is the canonical
        "Top 10 growth stocks" feed.
        """
        results = self.analyze_many(companies)
        if require_pass_filters:
            results = [r for r in results if not r.filtered_reasons]
        results.sort(
            key=lambda r: r.model_scores.get(
                "Growth",
                # Stocks without a growth score sort last via -inf.
                type("X", (), {"score": float("-inf")})(),
            ).score,
            reverse=True,
        )
        return results[:n]

    def top_ranked(
        self, companies: Iterable[Company], n: int = 50
    ) -> List[ValuationResult]:
        """
        Return top-N companies ranked by score — includes ALL companies,
        not just undervalued ones. This is what the "Top Picks" UI tab
        uses so users can see the full ranked list.
        """
        results = self.analyze_many(companies)
        results.sort(key=lambda r: (r.final_score, r.valuation_gap_pct), reverse=True)
        return results[:n]

    def compare(self, companies: Iterable[Company]) -> List[Dict]:
        """
        Side-by-side dictionary view, suitable for printing as a table
        or dumping to JSON. We deliberately keep this output flat so a
        future web/UI layer can render it without further wrangling.
        """
        rows = []
        for r in self.analyze_many(companies):
            rows.append({
                "Symbol": r.company.symbol,
                "Name": r.company.name,
                "Price": round(r.current_price, 2),
                "Intrinsic": round(r.intrinsic_value, 2),
                "Gap %": round(r.valuation_gap_pct, 1),
                "Score": round(r.final_score, 1),
                "Recommendation": r.recommendation,
            })
        return rows
