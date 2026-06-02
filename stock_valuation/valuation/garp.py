"""
Growth At Reasonable Price (GARP) — Peter Lynch style.

PEG = PE / earnings_growth_rate

Spec-driven calibration (v2):
  PEG < 1.0   -> excellent  (90-100)
  PEG 1.0-1.5 -> acceptable (60-90)
  PEG 1.5-2.0 -> stretched  (30-60)
  PEG > 2.0   -> penalize heavily (0-30, drops fast)

Lower PEG is better. Companies with non-positive growth get 0.
"""

from ..models import Company, ModelScore


class GARPModel:
    name = "GARP (Lynch)"

    def evaluate(self, c: Company) -> ModelScore:
        peg = c.peg_ratio  # property handles divide-by-zero -> inf

        # Map PEG -> 0-100 score with spec-driven anchor points.
        if peg == float("inf") or peg <= 0:
            score = 0.0
        elif peg < 0.5:
            score = 100.0
        elif peg < 1.0:
            # 0.5 -> 100, 1.0 -> 90  (excellent band)
            score = 100 - (peg - 0.5) * 20
        elif peg <= 1.5:
            # 1.0 -> 90, 1.5 -> 60  (acceptable)
            score = 90 - (peg - 1.0) * 60
        elif peg <= 2.0:
            # 1.5 -> 60, 2.0 -> 30  (stretched)
            score = 60 - (peg - 1.5) * 60
        elif peg <= 3.0:
            # 2.0 -> 30, 3.0 -> 5   (heavy penalty)
            score = 30 - (peg - 2.0) * 25
        else:
            # PEG > 3 — caps at 0
            score = 0.0

        score = max(0.0, min(100.0, score))
        peg_display = f"{peg:.2f}" if peg != float("inf") else "N/A"
        if peg <= 1.0:
            band = "excellent"
        elif peg <= 1.5:
            band = "acceptable"
        elif peg <= 2.0:
            band = "stretched"
        else:
            band = "expensive"
        notes = (
            f"PEG {peg_display} ({band}); "
            f"PE {c.pe_ratio:.1f} / Growth {c.growth_rate:.1f}%"
        )
        return ModelScore(self.name, score, weight=0.15, notes=notes)
