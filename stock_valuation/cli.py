"""
Command-line interface.

Subcommands:
  analyze   <symbol>          Detailed report for one company
  compare   <s1> <s2> ...     Side-by-side table for several companies
  top       [-n N]            Top-N undervalued names from the universe
  list                        List every company in the universe
  refresh                     Force-refetch the live cache

Data sources:
  --data <file>     Read from a JSON file (default: data/sample_companies.json)
  --live            Fetch live fundamentals from Yahoo Finance via yfinance
                    (requires `pip install yfinance`)
  --universe <id>   Stock universe in --live mode (nifty50 / nifty65)
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List

from .models import Company
from .analyzer import StockAnalyzer
from .data_provider import DataProvider, build_provider
from .universe import UNIVERSES, DEFAULT_UNIVERSE


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------
def load_companies(path: Path) -> Dict[str, Company]:
    """
    Load a JSON file of companies into a {symbol: Company} dict.
    Symbols are uppercased so lookup is case-insensitive.
    """
    if not path.exists():
        sys.exit(f"Data file not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        raw = json.load(f)

    companies: Dict[str, Company] = {}
    for entry in raw:
        try:
            c = Company(**entry)
        except TypeError as e:
            sys.exit(f"Bad record {entry.get('symbol', '?')}: {e}")
        companies[c.symbol.upper()] = c
    return companies


# ---------------------------------------------------------------------------
# Pretty printers
# ---------------------------------------------------------------------------
def print_report(result) -> None:
    """Detailed single-company report."""
    c = result.company
    bar = "=" * 64
    print(bar)
    print(f"  {c.name}  ({c.symbol})    Sector: {c.sector or 'N/A'}")
    print(bar)
    print(f"  Current Price       : Rs {result.current_price:,.2f}")
    print(f"  Intrinsic Value     : Rs {result.intrinsic_value:,.2f}")
    print(
        f"  Intrinsic Range     : Rs {result.intrinsic_value_low:,.2f}"
        f"  -  Rs {result.intrinsic_value_high:,.2f}"
    )
    gap_label = "undervalued" if result.valuation_gap_pct >= 0 else "overvalued"
    print(f"  Valuation Gap       : {result.valuation_gap_pct:+.1f}% ({gap_label})")
    print(f"  Final Score         : {result.final_score:.1f} / 100")
    print(f"  Recommendation      : >>>  {result.recommendation}  <<<")
    print()
    print("  Model breakdown:")
    print("  " + "-" * 60)
    for ms in result.model_scores.values():
        print(f"  {ms.name:<22} {ms.score:6.1f}/100  (w={ms.weight:.0%})")
        print(f"     {ms.notes}")
    print(bar)
    print()


def print_table(rows: List[Dict]) -> None:
    """Minimalist ASCII table — no third-party deps."""
    if not rows:
        print("(no rows)")
        return
    headers = list(rows[0].keys())
    widths = {h: max(len(h), *(len(str(r[h])) for r in rows)) for h in headers}

    def line(values):
        return "  " + "  ".join(str(v).ljust(widths[h]) for h, v in zip(headers, values))

    print(line(headers))
    print("  " + "  ".join("-" * widths[h] for h in headers))
    for r in rows:
        print(line([r[h] for h in headers]))
    print()


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------
def cmd_analyze(args, analyzer: StockAnalyzer, provider: DataProvider) -> None:
    sym = args.symbol.upper()
    company = provider.get(sym)
    if not company:
        sys.exit(f"Symbol '{sym}' not found.")
    print_report(analyzer.analyze(company))


def cmd_compare(args, analyzer: StockAnalyzer, provider: DataProvider) -> None:
    selected: List[Company] = []
    for sym in args.symbols:
        c = provider.get(sym.upper())
        if not c:
            sys.exit(f"Symbol '{sym}' not found.")
        selected.append(c)
    print_table(analyzer.compare(selected))


def cmd_top(args, analyzer: StockAnalyzer, provider: DataProvider) -> None:
    results = analyzer.top_undervalued(provider.get_all(), n=args.n)
    if not results:
        print("No undervalued companies found.")
        return
    rows = [
        {
            "Symbol": r.company.symbol,
            "Name": r.company.name,
            "Price": round(r.current_price, 2),
            "Intrinsic": round(r.intrinsic_value, 2),
            "Gap %": round(r.valuation_gap_pct, 1),
            "Score": round(r.final_score, 1),
            "Recommendation": r.recommendation,
        }
        for r in results
    ]
    print(f"Top {len(rows)} undervalued companies:\n")
    print_table(rows)


def cmd_list(args, analyzer: StockAnalyzer, provider: DataProvider) -> None:
    rows = [
        {"Symbol": c.symbol, "Name": c.name, "Sector": c.sector or "-"}
        for c in provider.get_all()
    ]
    print_table(rows)


def cmd_refresh(args, analyzer: StockAnalyzer, provider: DataProvider) -> None:
    """Force-refetch the live data cache."""
    if provider.label != "live":
        sys.exit("`refresh` only works with --live (no live source configured).")
    n = provider.refresh()
    print(f"Refreshed {n} companies in the live cache.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="stock-valuation",
        description="Indian stock valuation & analysis system",
    )
    # ---- data source flags (mutually compatible: --live overrides --data) ----
    p.add_argument(
        "--data",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "data" / "sample_companies.json",
        help="Path to a companies JSON file (used when --live is not set)",
    )
    p.add_argument(
        "--live",
        action="store_true",
        help="Fetch live fundamentals from Yahoo Finance (requires yfinance)",
    )
    p.add_argument(
        "--universe",
        choices=sorted(UNIVERSES.keys()),
        default=DEFAULT_UNIVERSE,
        help="Stock universe to use in --live mode (default: %(default)s)",
    )
    p.add_argument(
        "--refresh",
        action="store_true",
        help="Bypass the live cache and refetch every symbol",
    )

    sub = p.add_subparsers(dest="command", required=True)

    a = sub.add_parser("analyze", help="Detailed report for one company")
    a.add_argument("symbol")
    a.set_defaults(func=cmd_analyze)

    c = sub.add_parser("compare", help="Compare several companies side by side")
    c.add_argument("symbols", nargs="+")
    c.set_defaults(func=cmd_compare)

    t = sub.add_parser("top", help="Top-N undervalued companies in the universe")
    t.add_argument("-n", type=int, default=5)
    t.set_defaults(func=cmd_top)

    ls = sub.add_parser("list", help="List every company in the universe")
    ls.set_defaults(func=cmd_list)

    rf = sub.add_parser("refresh", help="Force-refetch the live cache")
    rf.set_defaults(func=cmd_refresh)

    return p


def main(argv=None) -> None:
    args = build_parser().parse_args(argv)
    provider = build_provider(
        live=args.live,
        data_path=args.data,
        universe=UNIVERSES.get(args.universe),
        force_refresh=args.refresh,
    )
    analyzer = StockAnalyzer()
    args.func(args, analyzer, provider)


if __name__ == "__main__":
    main()
