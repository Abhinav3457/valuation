"""
Live data source — Yahoo Finance via the `yfinance` package.

Responsibilities:
  * Translate a bare NSE symbol (e.g. "INFY") into a Yahoo ticker
    ("INFY.NS"), download fundamentals, and convert them into a
    `Company` instance the rest of the system understands.
  * Cache every successful fetch as JSON on disk so subsequent runs
    are instant. Cache entries expire after `CACHE_TTL_SECONDS`.
  * Be resilient to missing fields — Yahoo data is real and often
    incomplete; we fill conservative defaults instead of crashing.

This module is **optional**. The CLI and dashboard fall back to local
JSON files when `yfinance` is not installed, so the system still
works in zero-dependency mode.
"""

from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock
from typing import Iterable, List, Optional

from .models import Company

# yfinance is an optional dependency. We import lazily so the rest of
# the codebase keeps working without it.
try:
    import yfinance as yf
    YFINANCE_AVAILABLE = True
except Exception:  # pragma: no cover
    YFINANCE_AVAILABLE = False


log = logging.getLogger("stock_valuation.data_source")

# Where cached responses live. Each company is a separate JSON file
# so partial failures don't corrupt the dataset.
CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "cache"

# 24 hours — fundamentals don't change intraday, prices do but for
# valuation purposes daily refresh is more than enough.
CACHE_TTL_SECONDS = 24 * 3600


class LiveDataError(RuntimeError):
    """Raised when live data cannot be fetched and there's no fallback."""


# ---------------------------------------------------------------------------
# YFinance source
# ---------------------------------------------------------------------------
class YFinanceSource:
    """
    Pulls Indian-listed stock fundamentals from Yahoo Finance.

    Thread-safe: a single instance can be shared across the dashboard's
    request handler threads. Cache writes are atomic per-symbol.
    """

    SUFFIX = ".NS"  # NSE. Use ".BO" for BSE-only listings.

    def __init__(self, cache_dir: Path = CACHE_DIR, ttl: int = CACHE_TTL_SECONDS):
        if not YFINANCE_AVAILABLE:
            raise LiveDataError(
                "yfinance is not installed. Install it with:  pip install yfinance"
            )
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.ttl = ttl
        self._fetch_lock = Lock()  # serializes cache writes per process

    # ----- public API --------------------------------------------------
    def fetch(self, symbol: str, force: bool = False) -> Optional[Company]:
        """
        Return a Company for `symbol` (bare NSE ticker like "INFY"),
        or None if Yahoo has no usable data for it.

        Uses the disk cache when fresh unless `force=True`.
        """
        symbol = symbol.upper().strip()
        cache_path = self._cache_path(symbol)

        if not force and self._is_fresh(cache_path):
            try:
                return self._company_from_cache(cache_path, symbol)
            except Exception as e:
                log.debug("Cache load failed for %s (%s) — refetching", symbol, e)

        try:
            info = self._download(symbol)
        except Exception as e:
            log.warning("Download failed for %s: %s", symbol, e)
            return None
        if not info:
            return None

        self._save_cache(cache_path, info)
        return self._info_to_company(info, symbol)

    def fetch_many(
        self,
        symbols: Iterable[str],
        max_workers: int = 1,
        force: bool = False,
        progress=None,
    ) -> List[Company]:
        """
        Concurrent batch fetch. Returns successfully-loaded companies
        in the input order; failures are silently dropped (and logged).

        `progress` may be a callable accepting (done, total) for UI
        feedback during long fetches.
        """
        symbols = [s.upper().strip() for s in symbols]
        results: dict[str, Company] = {}
        total = len(symbols)
        done = 0

        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            future_to_sym = {
                ex.submit(self.fetch, s, force): s for s in symbols
            }
            for fut in as_completed(future_to_sym):
                sym = future_to_sym[fut]
                done += 1
                if progress:
                    try:
                        progress(done, total)
                    except Exception:
                        pass
                try:
                    c = fut.result()
                except Exception as e:
                    log.warning("%s: %s", sym, e)
                    continue
                if c is not None:
                    results[sym] = c

        # Preserve input order so downstream tables look stable.
        return [results[s] for s in symbols if s in results]

    # ----- internals ---------------------------------------------------
    def _yf_symbol(self, symbol: str) -> str:
        """Append the NSE suffix unless the caller already provided one."""
        return symbol if "." in symbol else symbol + self.SUFFIX

    def _download(self, symbol: str) -> Optional[dict]:
        """
        Hit Yahoo for one ticker and return its raw `.info` dict.
        Returns None when Yahoo has no usable record.
        """
        import requests
        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        })
        ticker = yf.Ticker(self._yf_symbol(symbol), session=session)
        try:
            info = ticker.info
        except Exception as e:
            raise LiveDataError(f"yfinance error: {e}")
        if not info:
            return None
        # Sanity-check: a real listing always has a market price.
        if not (info.get("regularMarketPrice") or info.get("currentPrice")):
            return None
        return info

    def _info_to_company(self, info: dict, fallback_symbol: str) -> Optional[Company]:
        """
        Convert a Yahoo `.info` dict into our Company dataclass.

        Currency note (important for Indian stocks):
          Yahoo returns *per-share* fields (regularMarketPrice, trailingEps,
          bookValue) in the listing currency (INR), but balance-sheet
          *totals* (totalDebt, freeCashflow, netIncomeToCommon, totalRevenue)
          in `financialCurrency`, which is often USD for Indian companies
          that file ADRs.

          To avoid mixing currencies — which produces garbage ratios — we
          use **per-share fields exclusively** and derive any "totals" we
          need from the share count, so everything stays in INR.

        Normalization:
          Yahoo gives ROE / growth as decimal fractions (0.32, -0.053).
          The valuation models expect percentage points (32, -5.3), so we
          multiply by 100 once at the boundary.
        """
        try:
            # Strip the exchange suffix for display.
            raw_symbol = (info.get("symbol") or fallback_symbol).upper()
            symbol = raw_symbol.replace(".NS", "").replace(".BO", "")

            price = info.get("regularMarketPrice") or info.get("currentPrice")
            if not price or price <= 0:
                return None

            shares = info.get("sharesOutstanding") or 0

            # Equity (INR): always derive from per-share book value × shares.
            # We deliberately ignore `totalStockholderEquity` because it
            # would be in USD for many Indian ADR-tracked tickers.
            book_per_share = info.get("bookValue")
            if book_per_share and shares:
                equity = book_per_share * shares
            else:
                # Last-resort placeholder so Company.__post_init__ accepts
                # the record. The deep-value model will still penalize it
                # via the PB ratio.
                equity = max(price * (shares or 1), 1.0)

            # Debt (INR): Yahoo's `debtToEquity` is reported as a percentage
            # (e.g. 10.531 means D/E = 0.105). Multiplying by our INR
            # equity gives an INR debt figure that's internally consistent
            # with equity, so the value-investing model's d/e check works.
            de_percent = info.get("debtToEquity")
            if de_percent is not None and equity > 0:
                debt = equity * (de_percent / 100.0)
            else:
                debt = 0.0

            # ROE — already a decimal, scale to percent.
            roe_raw = info.get("returnOnEquity")
            roe = (roe_raw * 100) if roe_raw is not None else 0.0

            # Growth — earnings preferred, then quarterly, then revenue.
            growth_raw = (
                info.get("earningsGrowth")
                if info.get("earningsGrowth") is not None
                else info.get("earningsQuarterlyGrowth")
                if info.get("earningsQuarterlyGrowth") is not None
                else info.get("revenueGrowth")
            )
            growth = (growth_raw * 100) if growth_raw is not None else 0.0

            # Cash-flow per share for DCF. We deliberately use trailing
            # EPS rather than `freeCashflow / shares` because Yahoo's
            # freeCashflow is in financialCurrency (often USD) while
            # the price (the DCF compares against) is in INR.
            # For capital-light Indian businesses EPS is a reasonable
            # FCF proxy; for capex-heavy ones it overstates true cash.
            trailing_eps = info.get("trailingEps")
            fcf_per_share = trailing_eps if (trailing_eps and trailing_eps > 0) else None

            pe = info.get("trailingPE") or info.get("forwardPE") or 0
            pb = info.get("priceToBook") or 0

            # Profit (INR), per-share derived. Used as a secondary DCF
            # fallback only — never reaches the valuation models when
            # `fcf_per_share` is set above.
            profit = (trailing_eps * shares) if (trailing_eps and shares) else 0

            # ---- Growth-model fields (v2 hybrid scoring) ----
            # Yahoo's `.info` only exposes single-period growth, not
            # multi-year CAGR. Use these as best-effort proxies; the
            # GrowthModel falls back to `growth_rate` if missing.
            rev_growth = info.get("revenueGrowth")
            earn_growth = info.get("earningsGrowth")
            revenue_cagr = (rev_growth * 100) if rev_growth is not None else None
            profit_cagr = (earn_growth * 100) if earn_growth is not None else None

            payout_ratio = info.get("payoutRatio")
            # Yahoo occasionally returns >1 (special dividends); cap below.
            if payout_ratio is not None:
                payout_ratio = max(0.0, min(1.5, float(payout_ratio)))

            return Company(
                name=info.get("longName") or info.get("shortName") or symbol,
                symbol=symbol,
                # Revenue isn't consumed by any valuation model; we
                # surface it for display only and leave it as 0 to
                # avoid mixing currencies in the dashboard table.
                revenue=0,
                profit=profit,
                debt=debt,
                equity=equity,
                pe_ratio=pe,
                pb_ratio=pb,
                roe=roe,
                growth_rate=growth,
                current_price=price,
                cash_flow=fcf_per_share,
                sector=info.get("sector"),
                shares_outstanding=shares or None,
                # Growth fields
                revenue_cagr=revenue_cagr,
                profit_cagr=profit_cagr,
                payout_ratio=payout_ratio,
                # Reinvestment derived inside GrowthModel from payout if
                # absent here; we leave it None so the model can choose.
                reinvestment_rate=None,
            )
        except Exception as e:
            log.warning("Conversion failed for %s: %s", fallback_symbol, e)
            return None

    # ----- cache helpers -----------------------------------------------
    def _cache_path(self, symbol: str) -> Path:
        # Strip filesystem-unfriendly characters (e.g. M&M -> M_M).
        safe = "".join(c if c.isalnum() else "_" for c in symbol)
        return self.cache_dir / f"{safe}.json"

    def _is_fresh(self, path: Path) -> bool:
        if not path.exists():
            return False
        return (time.time() - path.stat().st_mtime) < self.ttl

    def _save_cache(self, path: Path, info: dict) -> None:
        # Yahoo occasionally embeds non-JSON-serializable types
        # (e.g. numpy scalars). Sanitize.
        clean: dict = {}
        for k, v in info.items():
            try:
                json.dumps(v)
                clean[k] = v
            except (TypeError, ValueError):
                clean[k] = str(v)
        with self._fetch_lock:
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(clean, indent=2))
            tmp.replace(path)  # atomic on POSIX & Windows

    def _company_from_cache(self, path: Path, symbol: str) -> Optional[Company]:
        info = json.loads(path.read_text())
        return self._info_to_company(info, symbol)

    # ----- maintenance -------------------------------------------------
    def cache_age(self, symbol: str) -> Optional[float]:
        """Seconds since the cache file was written, or None if absent."""
        p = self._cache_path(symbol)
        if not p.exists():
            return None
        return time.time() - p.stat().st_mtime

    def clear_cache(self) -> int:
        """Delete every cache file. Returns the count removed."""
        n = 0
        for f in self.cache_dir.glob("*.json"):
            f.unlink()
            n += 1
        return n
