"""
Stock Valuation System for Indian Listed Companies.

A modular, extensible framework that combines multiple proven
investor strategies (Buffett, Lynch, Graham, DCF) into a single
data-driven recommendation.
"""

from .models import Company, ValuationResult
from .analyzer import StockAnalyzer

__all__ = ["Company", "ValuationResult", "StockAnalyzer"]
__version__ = "1.0.0"
