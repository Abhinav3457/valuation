"""
Deep Value model — Benjamin Graham style.

Graham's classical screen for the "defensive investor":
  * PE below 15 — earnings yield of at least ~6.7%.
  * PB below 1.5 — paying close to book value.

Combined, the product PE * PB should ideally stay under 22.5
(Graham's well-known "number"). We score both criteria together so
that a stock which clears one but spectacularly fails the other gets
penalized.
"""

from ..models import Company, ModelScore


class DeepValueModel:
    name = "Deep Value (Graham)"

    PE_TARGET = 15.0
    PB_TARGET = 1.5
    GRAHAM_NUMBER = 22.5  # PE * PB

    def evaluate(self, c: Company) -> ModelScore:
        # Sub-scores: 100 if at/below target, decaying linearly to 0
        # at 2x the target.
        pe_score = self._linear_score(c.pe_ratio, self.PE_TARGET, 2 * self.PE_TARGET)
        pb_score = self._linear_score(c.pb_ratio, self.PB_TARGET, 2 * self.PB_TARGET)

        combined = c.pe_ratio * c.pb_ratio
        graham_score = self._linear_score(
            combined, self.GRAHAM_NUMBER, 2 * self.GRAHAM_NUMBER
        )

        # Average of three signals — gives a smoother score than the
        # binary pass/fail check Graham originally used.
        score = (pe_score + pb_score + graham_score) / 3
        score = max(0.0, min(100.0, score))

        notes = (
            f"PE {c.pe_ratio:.1f} (<{self.PE_TARGET}), "
            f"PB {c.pb_ratio:.2f} (<{self.PB_TARGET}), "
            f"PE*PB {combined:.1f} (<{self.GRAHAM_NUMBER})"
        )
        return ModelScore(self.name, score, weight=0.20, notes=notes)

    @staticmethod
    def _linear_score(value: float, good: float, bad: float) -> float:
        """100 at or below `good`, 0 at or above `bad`, linear between."""
        if value <= good:
            return 100.0
        if value >= bad:
            return 0.0
        return (bad - value) / (bad - good) * 100
