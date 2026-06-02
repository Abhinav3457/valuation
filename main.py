"""
Top-level entry point.

Allows running the app as `python main.py <command> ...` from the
project root without needing to remember the package path.
"""

from stock_valuation.cli import main

if __name__ == "__main__":
    main()
