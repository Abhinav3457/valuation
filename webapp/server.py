"""
Stdlib HTTP server — stock valuation dashboard.

    pip install yfinance             # one-time setup
    python dashboard.py              # starts at http://localhost:8000
"""

import json
import sys
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stock_valuation.models import Company         # noqa: E402
from stock_valuation.analyzer import StockAnalyzer  # noqa: E402
from stock_valuation.universe import NIFTY_50, ALL_NSE, PRIORITY_ORDERED, UNIVERSES  # noqa: E402

STATIC_DIR = Path(__file__).resolve().parent / "static"
CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "cache"
SESSION_PATH = Path(__file__).resolve().parent.parent / "data" / "session.json"
ANALYZER = StockAnalyzer()

# In-memory store. Loaded from SESSION_PATH on startup so the user's
# last session survives a server restart. Repopulated whenever the
# user uploads a CSV or clicks "Fetch".
UPLOADED: dict[str, Company] = {}


def _save_session() -> None:
    """
    Persist UPLOADED to disk so the next server start (or a browser
    refresh after we crash) can resume from where the user left off.
    """
    try:
        SESSION_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 2,
            "companies": [
                {
                    "name": c.name, "symbol": c.symbol,
                    "revenue": c.revenue, "profit": c.profit,
                    "debt": c.debt, "equity": c.equity,
                    "pe_ratio": c.pe_ratio, "pb_ratio": c.pb_ratio,
                    "roe": c.roe, "growth_rate": c.growth_rate,
                    "current_price": c.current_price,
                    "cash_flow": c.cash_flow, "sector": c.sector,
                    "shares_outstanding": c.shares_outstanding,
                    # v2 growth fields (None for older sessions)
                    "revenue_cagr": c.revenue_cagr,
                    "profit_cagr": c.profit_cagr,
                    "payout_ratio": c.payout_ratio,
                    "reinvestment_rate": c.reinvestment_rate,
                    "operating_cashflow": c.operating_cashflow,
                }
                for c in UPLOADED.values()
            ],
        }
        tmp = SESSION_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload))
        tmp.replace(SESSION_PATH)
    except Exception as e:
        print(f"  [warn] session save failed: {e}", file=sys.stderr)


def _clear_session() -> None:
    """Wipe loaded data and the on-disk session file."""
    global UPLOADED
    UPLOADED = {}
    if SESSION_PATH.is_file():
        SESSION_PATH.unlink()


def _load_session() -> None:
    """Restore UPLOADED from the previous session, if any."""
    global UPLOADED
    if not SESSION_PATH.is_file():
        return
    try:
        data = json.loads(SESSION_PATH.read_text())
        loaded = {}
        for d in data.get("companies", []):
            try:
                c = Company(**d)
                loaded[c.symbol] = c
            except Exception:
                continue
        if loaded:
            UPLOADED = loaded
            print(f"  Resumed previous session: {len(loaded)} companies.")
    except Exception as e:
        print(f"  [warn] session load failed: {e}", file=sys.stderr)

# Background fetch state — so the UI can poll progress.
# `loaded` tracks how many actually produced usable data (may be < done
# if some symbols 404 or fail to parse). UI uses this for partial display.
FETCH_STATE = {"running": False, "done": 0, "total": 0, "loaded": 0, "status": "idle"}

MIME = {
    ".html": "text/html; charset=utf-8",
    ".css":  "text/css; charset=utf-8",
    ".js":   "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".csv":  "text/csv; charset=utf-8",
    ".svg":  "image/svg+xml",
}

# Template CSV — the first row is headers, the second is an example
# so users see the expected format immediately.
TEMPLATE_CSV = """\
name,symbol,sector,current_price,pe_ratio,pb_ratio,roe,growth_rate,revenue,profit,debt,equity,cash_flow,shares_outstanding
Infosys,INFY,IT,1480.00,24.5,7.8,31.8,12.0,153670,24108,7900,75300,60.5,4150
TCS,TCS,IT,3850.00,28.0,14.0,47.2,11.0,240893,45908,8200,95000,130.0,3660
HDFC Bank,HDFCBANK,Financial Services,1520.00,18.5,2.7,16.5,16.0,280000,60000,220000,410000,85.0,7600
"""


class DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def _send_json(self, payload, code=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", MIME[".json"])
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_static(self, rel_path):
        path = (STATIC_DIR / rel_path).resolve()
        try:
            path.relative_to(STATIC_DIR)
        except ValueError:
            self.send_error(403)
            return
        if not path.is_file():
            self.send_error(404)
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", MIME.get(path.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ---- GET ---------------------------------------------------------------
    def do_GET(self):
        url = urlparse(self.path)
        path = url.path
        qs = parse_qs(url.query)

        if path in ("/", "/index.html"):
            return self._send_static("index.html")
        if path in ("/style.css", "/app.js"):
            return self._send_static(path.lstrip("/"))

        # Download blank CSV template
        if path == "/api/template":
            body = TEMPLATE_CSV.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", MIME[".csv"])
            self.send_header("Content-Disposition",
                             'attachment; filename="stock_valuation_template.csv"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        # How many companies are loaded?
        if path == "/api/meta":
            session_age = None
            if SESSION_PATH.is_file():
                import time
                session_age = int(time.time() - SESSION_PATH.stat().st_mtime)
            return self._send_json({
                "count": len(UPLOADED),
                "universe_size": len(PRIORITY_ORDERED),
                "session_age_seconds": session_age,
            })

        # Wipe loaded data and the saved session (start fresh).
        if path == "/api/clear":
            try:
                _clear_session()
            except Exception as e:
                return self._send_json({"error": str(e)}, 500)
            return self._send_json({"cleared": True})

        # Analyze one symbol
        if path.startswith("/api/analyze/"):
            sym = path.rsplit("/", 1)[-1].upper()
            if sym not in UPLOADED:
                return self._send_json({"error": f"Symbol '{sym}' not found"}, 404)
            return self._send_json(ANALYZER.analyze(UPLOADED[sym]).to_dict())

        # Full detail for one symbol — analysis + raw Yahoo Finance data
        if path.startswith("/api/detail/"):
            sym = path.rsplit("/", 1)[-1].upper()
            if sym not in UPLOADED:
                return self._send_json({"error": f"Symbol '{sym}' not found"}, 404)
            result = ANALYZER.analyze(UPLOADED[sym]).to_dict()
            # Attach raw Yahoo Finance cache data if available
            safe = "".join(c if c.isalnum() else "_" for c in sym)
            cache_path = CACHE_DIR / f"{safe}.json"
            yahoo = {}
            if cache_path.is_file():
                try:
                    yahoo = json.loads(cache_path.read_text())
                except Exception:
                    pass
            result["yahoo"] = yahoo
            return self._send_json(result)

        # Analyze all uploaded companies
        if path == "/api/analyze-all":
            results = ANALYZER.analyze_many(UPLOADED.values())
            return self._send_json([r.to_dict() for r in results])

        # Top N — ranked by score, includes ALL companies (not filtered)
        if path == "/api/top":
            try:
                n = int(qs.get("n", ["50"])[0])
            except ValueError:
                n = 50
            results = ANALYZER.top_ranked(UPLOADED.values(), n=n)
            return self._send_json([r.to_dict() for r in results])

        # Compare specific symbols
        if path == "/api/compare":
            raw = qs.get("symbols", [""])[0]
            symbols = [s.strip().upper() for s in raw.split(",") if s.strip()]
            companies, missing = [], []
            for s in symbols:
                if s in UPLOADED:
                    companies.append(UPLOADED[s])
                else:
                    missing.append(s)
            if missing:
                return self._send_json(
                    {"error": f"Unknown: {', '.join(missing)}"}, 400)
            results = ANALYZER.analyze_many(companies)
            return self._send_json([r.to_dict() for r in results])

        # Fetch progress (polled by UI during background fetch)
        if path == "/api/fetch-status":
            return self._send_json(FETCH_STATE)

        # Trigger NIFTY 50 fetch from Yahoo Finance
        # ?limit=N to fetch only the first N symbols (default: all 50)
        if path == "/api/fetch-nifty":
            if FETCH_STATE["running"]:
                return self._send_json({"error": "Fetch already in progress"}, 409)
            try:
                limit = int(qs.get("limit", [str(len(PRIORITY_ORDERED))])[0])
            except ValueError:
                limit = len(PRIORITY_ORDERED)
            limit = max(1, min(limit, len(PRIORITY_ORDERED)))
            symbols = PRIORITY_ORDERED[:limit]
            # Set state eagerly so the very first poll sees "fetching"
            FETCH_STATE["running"] = True
            FETCH_STATE["total"] = len(symbols)
            FETCH_STATE["done"] = 0
            FETCH_STATE["loaded"] = 0
            FETCH_STATE["status"] = "fetching"
            threading.Thread(target=_background_fetch, args=(symbols,), daemon=True).start()
            return self._send_json({"message": "Fetch started", "total": len(symbols)})

        self.send_error(404)

    # ---- POST: upload companies as JSON array ------------------------------
    def do_POST(self):
        url = urlparse(self.path)
        if url.path != "/api/upload":
            self.send_error(404)
            return

        # Read the body
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return self._send_json({"error": "Empty request body"}, 400)
        body = self.rfile.read(length)

        try:
            rows = json.loads(body)
        except json.JSONDecodeError as e:
            return self._send_json({"error": f"Invalid JSON: {e}"}, 400)

        if not isinstance(rows, list) or not rows:
            return self._send_json({"error": "Expected a JSON array of companies"}, 400)

        global UPLOADED
        errors = []
        loaded = {}
        for i, row in enumerate(rows, 1):
            try:
                # Coerce numeric fields — users may paste strings from Excel
                for field in ("current_price", "pe_ratio", "pb_ratio", "roe",
                              "growth_rate", "revenue", "profit", "debt",
                              "equity", "cash_flow", "shares_outstanding"):
                    if field in row and row[field] is not None and row[field] != "":
                        row[field] = float(row[field])
                    elif field in ("cash_flow", "shares_outstanding"):
                        row[field] = None  # optional
                    elif field not in row:
                        row[field] = 0

                c = Company(
                    name=str(row.get("name", f"Company {i}")),
                    symbol=str(row.get("symbol", f"CO{i}")).upper().strip(),
                    sector=str(row.get("sector", "")) or None,
                    revenue=float(row.get("revenue", 0)),
                    profit=float(row.get("profit", 0)),
                    debt=float(row.get("debt", 0)),
                    equity=float(row.get("equity", 1)),
                    pe_ratio=float(row.get("pe_ratio", 0)),
                    pb_ratio=float(row.get("pb_ratio", 0)),
                    roe=float(row.get("roe", 0)),
                    growth_rate=float(row.get("growth_rate", 0)),
                    current_price=float(row["current_price"]),
                    cash_flow=row.get("cash_flow"),
                    shares_outstanding=row.get("shares_outstanding"),
                )
                loaded[c.symbol] = c
            except Exception as e:
                errors.append(f"Row {i} ({row.get('symbol', '?')}): {e}")

        UPLOADED = loaded
        _save_session()

        return self._send_json({
            "loaded": len(loaded),
            "errors": errors,
        })


def _background_fetch(symbols):
    """
    Fetch given symbols from Yahoo Finance in a background thread,
    collecting results **incrementally** into UPLOADED so partial data
    is preserved if the batch is interrupted or a symbol fails.

    Updates FETCH_STATE so the UI can show a live progress bar and
    accurate loaded-vs-failed counts.
    """
    global UPLOADED

    # Start fresh — previous state is replaced on each new fetch.
    UPLOADED = {}

    try:
        from stock_valuation.data_source import YFinanceSource
        source = YFinanceSource()
    except Exception as e:
        FETCH_STATE["status"] = f"error: {e}"
        FETCH_STATE["running"] = False
        print(f"  Fetch setup error: {e}", file=sys.stderr)
        return

    from concurrent.futures import ThreadPoolExecutor, as_completed

    total = len(symbols)
    done = 0
    loaded = 0
    failed_symbols = []

    try:
        with ThreadPoolExecutor(max_workers=8) as ex:
            future_to_sym = {ex.submit(source.fetch, s): s for s in symbols}
            for fut in as_completed(future_to_sym):
                sym = future_to_sym[fut]
                done += 1
                FETCH_STATE["done"] = done
                try:
                    c = fut.result()
                except Exception as e:
                    failed_symbols.append(sym)
                    print(f"  {sym}: {e}", file=sys.stderr)
                    continue
                if c is not None:
                    # Persist immediately so partial results survive
                    # any mid-batch crash.
                    UPLOADED[c.symbol] = c
                    loaded += 1
                    FETCH_STATE["loaded"] = loaded
                else:
                    failed_symbols.append(sym)

        FETCH_STATE["status"] = "done"
        print(f"  Fetched {loaded}/{total} companies from Yahoo Finance.")
        if failed_symbols:
            print(f"  Skipped: {', '.join(failed_symbols[:10])}"
                  + (f" (+{len(failed_symbols) - 10} more)" if len(failed_symbols) > 10 else ""))
    except Exception as e:
        # Partial data is already in UPLOADED — just record that we stopped early.
        FETCH_STATE["status"] = f"partial: fetched {loaded}/{total}, stopped on: {e}"
        print(f"  Fetch interrupted ({loaded}/{total} loaded): {e}", file=sys.stderr)
    finally:
        FETCH_STATE["running"] = False
        # Persist whatever made it into UPLOADED so the user can
        # resume after browser close or server restart.
        if UPLOADED:
            _save_session()


def serve(host="127.0.0.1", port=8000):
    _load_session()
    server = HTTPServer((host, port), DashboardHandler)
    url = f"http://{host}:{port}"
    print(f"\nStock Valuation Dashboard running at {url}")
    print("Press Ctrl+C to stop.\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.server_close()


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    args = p.parse_args()
    serve(args.host, args.port)
