#!/usr/bin/env python
"""
screen_growth.py — Growth-focused stock screener (pandas CLI).

Wraps the v2 hybrid scoring engine in a clean DataFrame interface so
quant-style users can pipe a CSV in, get a ranked DataFrame out.

Usage:
    python screen_growth.py input.csv [-o output.csv] [--top 10]
    python screen_growth.py --from-cache       # reuse data/session.json

Input CSV columns (extras ignored, missing values handled gracefully):
    name, symbol, sector, current_price, pe_ratio, pb_ratio,
    roe, growth_rate, equity, debt, profit, revenue,
    cash_flow, shares_outstanding,
    revenue_cagr, profit_cagr, payout_ratio,
    reinvestment_rate, operating_cashflow

Output columns:
    Symbol, Name, Sector, Final Score,
    Growth Score, Value Score, GARP Score, DCF Score, Sector Score,
    Recommendation, Flag, Filter Reasons,
    Current Price, Intrinsic Value, Gap %

The script also prints:
  - Top 10 growth stocks (highest Growth sub-score, filters passed)
  - Counts by recommendation and flag
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

import pandas as pd

# Make the package importable regardless of cwd.
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stock_valuation.models import Company  # noqa: E402
from stock_valuation.analyzer import StockAnalyzer  # noqa: E402
from stock_valuation.scoring import hard_filter_reasons  # noqa: E402


# ---------------------------------------------------------------------------
# Input adapters
# ---------------------------------------------------------------------------
def _to_optional_float(v):
    if v is None or pd.isna(v) or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _to_float(v, default=0.0):
    f = _to_optional_float(v)
    return f if f is not None else default


def row_to_company(row: dict) -> Optional[Company]:
    """
    Convert a single CSV row (as dict) to a Company. Returns None if the
    row is unusable (e.g. missing price/equity). Logs to stderr.
    """
    try:
        return Company(
            name=str(row.get("name") or row.get("symbol") or "?"),
            symbol=str(row["symbol"]).upper().strip(),
            sector=(str(row["sector"]).strip() if row.get("sector") not in (None, "") else None),
            revenue=_to_float(row.get("revenue")),
            profit=_to_float(row.get("profit")),
            debt=_to_float(row.get("debt")),
            equity=_to_float(row.get("equity"), 1.0),
            pe_ratio=_to_float(row.get("pe_ratio")),
            pb_ratio=_to_float(row.get("pb_ratio")),
            roe=_to_float(row.get("roe")),
            growth_rate=_to_float(row.get("growth_rate")),
            current_price=_to_float(row["current_price"]),
            cash_flow=_to_optional_float(row.get("cash_flow")),
            shares_outstanding=_to_optional_float(row.get("shares_outstanding")),
            revenue_cagr=_to_optional_float(row.get("revenue_cagr")),
            profit_cagr=_to_optional_float(row.get("profit_cagr")),
            payout_ratio=_to_optional_float(row.get("payout_ratio")),
            reinvestment_rate=_to_optional_float(row.get("reinvestment_rate")),
            operating_cashflow=_to_optional_float(row.get("operating_cashflow")),
        )
    except Exception as e:
        print(f"  [skip] {row.get('symbol', '?')}: {e}", file=sys.stderr)
        return None


def load_companies_from_csv(path: Path) -> list[Company]:
    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]
    out = []
    for _, row in df.iterrows():
        c = row_to_company(row.to_dict())
        if c is not None:
            out.append(c)
    return out


def load_companies_from_session(path: Path) -> list[Company]:
    """Load from the dashboard's persisted session.json."""
    data = json.loads(path.read_text())
    out = []
    for d in data.get("companies", []):
        # Older sessions may stash strings where floats are now expected.
        # Coerce the numeric fields up front so a single bad row doesn't
        # crash the screener.
        for k in ("revenue", "profit", "debt", "equity", "pe_ratio",
                  "pb_ratio", "roe", "growth_rate", "current_price",
                  "cash_flow", "shares_outstanding", "revenue_cagr",
                  "profit_cagr", "payout_ratio", "reinvestment_rate",
                  "operating_cashflow"):
            if k in d:
                d[k] = _to_optional_float(d[k]) if k in (
                    "cash_flow", "shares_outstanding", "revenue_cagr",
                    "profit_cagr", "payout_ratio", "reinvestment_rate",
                    "operating_cashflow",
                ) else _to_float(d[k])
        try:
            out.append(Company(**d))
        except Exception as e:
            print(f"  [skip] {d.get('symbol', '?')}: {e}", file=sys.stderr)
    return out


# ---------------------------------------------------------------------------
# Scoring -> DataFrame
# ---------------------------------------------------------------------------
def score_to_dataframe(companies: list[Company]) -> pd.DataFrame:
    analyzer = StockAnalyzer()
    rows = []
    for c in companies:
        try:
            r = analyzer.analyze(c)
        except Exception as e:
            print(f"  [skip] {c.symbol}: {e}", file=sys.stderr)
            continue
        ms = r.model_scores
        rows.append({
            "Symbol":          r.company.symbol,
            "Name":            r.company.name,
            "Sector":          r.company.sector or "",
            "Final Score":     round(r.final_score, 2),
            "Growth Score":    round(ms["Growth"].score, 2) if "Growth" in ms else None,
            "Value Score":     round(ms["Value Investing"].score, 2) if "Value Investing" in ms else None,
            "GARP Score":      round(ms["GARP (Lynch)"].score, 2) if "GARP (Lynch)" in ms else None,
            "DCF Score":       round(ms["DCF"].score, 2) if "DCF" in ms else None,
            "Sector Score":    round(ms["Sector Strength"].score, 2) if "Sector Strength" in ms else None,
            "Recommendation":  r.recommendation,
            "Flag":            r.flag or "",
            "Filter Reasons":  "; ".join(r.filtered_reasons),
            "Current Price":   round(r.current_price, 2),
            "Intrinsic Value": round(r.intrinsic_value, 2),
            "Gap %":           round(r.valuation_gap_pct, 2),
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    p = argparse.ArgumentParser(description="Growth-focused stock screener.")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("input", nargs="?", help="Path to input CSV.")
    src.add_argument("--from-cache", action="store_true",
                     help="Use the dashboard's persisted session.json.")
    p.add_argument("-o", "--output", help="Where to write the ranked CSV.")
    p.add_argument("--top", type=int, default=10,
                   help="How many top growth stocks to highlight (default: 10).")
    p.add_argument("--keep-filtered", action="store_true",
                   help="Include companies that failed hard filters in the output.")
    args = p.parse_args()

    # Load
    if args.from_cache:
        session = ROOT / "data" / "session.json"
        if not session.is_file():
            print(f"No session at {session}. Run the dashboard first.", file=sys.stderr)
            return 1
        companies = load_companies_from_session(session)
        print(f"Loaded {len(companies)} companies from session cache.")
    else:
        path = Path(args.input)
        if not path.is_file():
            print(f"Input CSV not found: {path}", file=sys.stderr)
            return 1
        companies = load_companies_from_csv(path)
        print(f"Loaded {len(companies)} companies from {path}.")

    if not companies:
        print("No usable rows.", file=sys.stderr)
        return 1

    # Score
    df = score_to_dataframe(companies)
    if df.empty:
        print("Scoring produced no rows.", file=sys.stderr)
        return 1

    # Filter
    if not args.keep_filtered:
        before = len(df)
        df = df[df["Recommendation"] != "Filtered"].copy()
        print(f"Hard filters dropped {before - len(df)} of {before} companies.")

    # Rank by Final Score
    df = df.sort_values(by="Final Score", ascending=False).reset_index(drop=True)

    # Print summary tables
    print("\n=== Recommendation breakdown ===")
    print(df["Recommendation"].value_counts().to_string())

    if df["Flag"].astype(bool).any():
        print("\n=== Flag breakdown ===")
        print(df[df["Flag"] != ""]["Flag"].value_counts().to_string())

    print(f"\n=== Top {args.top} by Growth Score (filters passed) ===")
    top_growth = (
        df[df["Growth Score"].notna()]
        .sort_values(by="Growth Score", ascending=False)
        .head(args.top)
    )
    cols = ["Symbol", "Name", "Sector", "Growth Score", "Final Score", "Recommendation", "Flag"]
    print(top_growth[cols].to_string(index=False))

    print(f"\n=== Top {args.top} by Final Score ===")
    print(df.head(args.top)[cols].to_string(index=False))

    # Output
    if args.output:
        out = Path(args.output)
        df.to_csv(out, index=False)
        print(f"\nWrote ranked DataFrame to {out} ({len(df)} rows).")
    else:
        print(f"\n(Pass -o output.csv to save the full ranked DataFrame; {len(df)} rows total.)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
