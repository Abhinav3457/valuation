"""
DataProvider abstraction.

Lets the rest of the system (CLI, web server) ask for companies
without caring whether they came from a static JSON file or a live
network fetch. Two implementations:

  * FileDataProvider — backed by data/sample_companies.json (default)
  * LiveDataProvider — backed by Yahoo Finance via YFinanceSource

Both implement the same minimal interface:
    get(symbol)   -> Company | None
    get_all()     -> list[Company]
"""

from __future__ import annotations

import sys
from pathlib import Path
from threading import Lock
from typing import Iterable, List, Optional

from .models import Company


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------
class DataProvider:
    label: str = "data"

    def get(self, symbol: str) -> Optional[Company]:
        raise NotImplementedError

    def get_all(self) -> List[Company]:
        raise NotImplementedError

    def refresh(self) -> int:
        """Optional: force a refetch. Returns number of companies loaded."""
        return len(self.get_all())


# ---------------------------------------------------------------------------
# File-backed provider (existing behavior)
# ---------------------------------------------------------------------------
class FileDataProvider(DataProvider):
    label = "file"

    def __init__(self, path: Path):
        # Local import to avoid circular references with cli.py.
        from .cli import load_companies
        self.path = Path(path)
        self.companies = load_companies(self.path)

    def get(self, symbol: str) -> Optional[Company]:
        return self.companies.get(symbol.upper())

    def get_all(self) -> List[Company]:
        return list(self.companies.values())


# ---------------------------------------------------------------------------
# Live provider (Yahoo Finance via yfinance)
# ---------------------------------------------------------------------------
class LiveDataProvider(DataProvider):
    """
    Lazily fetches the configured universe on first `get_all()` call.
    Per-symbol `get(...)` calls hit Yahoo directly (and cache for next
    time) so single-company analyses don't pay the universe-fetch cost.
    """

    label = "live"

    def __init__(self, universe: Iterable[str], force_refresh: bool = False, prefetch: bool = False):
        # Imported here so the rest of the codebase doesn't require
        # yfinance to be installed.
        from .data_source import YFinanceSource
        self.source = YFinanceSource()
        self.universe = list(universe)
        self.force_refresh = force_refresh
        self._all: Optional[List[Company]] = None
        self._lock = Lock()
        if prefetch:
            self._fetch_universe()

    # ----- public ------------------------------------------------------
    def get(self, symbol: str) -> Optional[Company]:
        sym = symbol.upper()
        # If we've already fetched the universe, look it up there
        # first — same data the dashboard's browse view sees.
        if self._all is not None:
            for c in self._all:
                if c.symbol == sym:
                    return c
        # Otherwise hit Yahoo (uses disk cache when fresh).
        return self.source.fetch(sym, force=self.force_refresh)

    def get_all(self) -> List[Company]:
        if self._all is None:
            self._fetch_universe()
        return self._all or []

    def refresh(self) -> int:
        """Force a refetch of the entire universe, bypassing cache."""
        with self._lock:
            self._all = None
            self.force_refresh = True
            self._fetch_universe()
            self.force_refresh = False
        return len(self._all or [])

    # ----- internals ---------------------------------------------------
    def _fetch_universe(self) -> None:
        with self._lock:
            if self._all is not None:
                return
            total = len(self.universe)
            print(
                f"Fetching {total} companies from Yahoo Finance "
                f"(this may take 20-60 seconds on first run)...",
                file=sys.stderr,
            )

            def _progress(done: int, total: int) -> None:
                # Carriage-return progress bar — cheap and works in
                # any terminal, no extra dependency.
                bar_w = 30
                filled = int(bar_w * done / total)
                bar = "#" * filled + "-" * (bar_w - filled)
                print(
                    f"\r  [{bar}] {done}/{total}",
                    end="",
                    file=sys.stderr,
                    flush=True,
                )

            self._all = self.source.fetch_many(
                self.universe,
                force=self.force_refresh,
                progress=_progress,
            )
            print("", file=sys.stderr)  # newline after the progress bar
            print(
                f"  -> {len(self._all)}/{total} companies loaded successfully.",
                file=sys.stderr,
            )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------
def build_provider(
    *,
    live: bool = False,
    data_path: Optional[Path] = None,
    universe: Optional[Iterable[str]] = None,
    force_refresh: bool = False,
    prefetch: bool = False,
) -> DataProvider:
    """
    Construct the right provider based on caller intent.

    Caller is responsible for picking sane defaults — this function
    just dispatches.
    """
    if live:
        from .universe import UNIVERSES, DEFAULT_UNIVERSE
        symbols = list(universe) if universe else UNIVERSES[DEFAULT_UNIVERSE]
        return LiveDataProvider(symbols, force_refresh=force_refresh, prefetch=prefetch)
    if data_path is None:
        raise ValueError("data_path is required when live=False")
    return FileDataProvider(data_path)
