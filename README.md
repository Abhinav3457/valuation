# Stock Valuation System (Indian Listed Companies)

A modular Python application that values Indian listed companies by
combining five well-known investment frameworks into a single
data-driven recommendation.

The aim is **clarity over complexity**: even a beginner investor should
be able to read the output and understand *why* a stock is rated the way
it is.

---

## What it does

For each company you provide, the system:

1. Runs five valuation models:
   - **Value Investing (Buffett)** — high ROE, consistent growth, low debt
   - **GARP (Peter Lynch)** — PEG ratio (PE / growth)
   - **Deep Value (Benjamin Graham)** — PE < 15, PB < 1.5, Graham's number
   - **Discounted Cash Flow (DCF)** — 10-year two-stage DCF with terminal value
   - **Sector Analysis** — boost/penalty for growing vs declining sectors
2. Combines them into a weighted score (0–100):

   | Model            | Weight |
   |------------------|--------|
   | Value Investing  | 25%    |
   | GARP             | 20%    |
   | Deep Value       | 20%    |
   | DCF              | 25%    |
   | Sector Strength  | 10%    |

3. Outputs:
   - **Intrinsic value** (from DCF) with a low/high band
   - **Current price**
   - **Valuation gap %** (positive = undervalued)
   - **Final score** out of 100
   - **Recommendation**: `Strong Buy` / `Buy` / `Hold` / `Avoid`

---

## Project structure

```
.
├── main.py                          # CLI entry point
├── dashboard.py                     # Web dashboard launcher
├── data/
│   └── sample_companies.json        # 12 Indian companies for testing
├── stock_valuation/                 # Core valuation engine
│   ├── __init__.py
│   ├── models.py                    # Company & ValuationResult dataclasses
│   ├── scoring.py                   # Composite scoring engine
│   ├── analyzer.py                  # Orchestrator (single & batch)
│   ├── cli.py                       # Command-line interface
│   └── valuation/                   # One file per investment philosophy
│       ├── value_investing.py       # Buffett style
│       ├── garp.py                  # Peter Lynch style
│       ├── deep_value.py            # Benjamin Graham style
│       ├── dcf.py                   # Discounted cash flow
│       └── sector.py                # Sector overlay
├── stock_valuation/
│   ├── data_source.py               # Yahoo Finance fetcher (yfinance) + cache
│   ├── data_provider.py             # File vs Live provider abstraction
│   └── universe.py                  # NIFTY 50 / NIFTY 65 ticker lists
├── data/
│   └── cache/                       # Per-symbol JSON cache for live mode
├── requirements.txt                 # yfinance (only needed for --live)
└── webapp/                          # Local web dashboard
    ├── server.py                    # Stdlib HTTP + JSON API
    └── static/                      # index.html / style.css / app.js
```

The layers are deliberately separated:

- **Input** — `data/*.json` and the `Company` dataclass
- **Processing** — `valuation/*.py`, `scoring.py`, `analyzer.py`
- **Output** — `cli.py` and `ValuationResult.to_dict()`

---

## Requirements

- Python 3.9+
- **Sample-data mode is zero-dependency** — pure standard library.
- **Live-data mode** (`--live`) needs `yfinance`:
  ```bash
  pip install -r requirements.txt
  ```

---

## How to run

### Web dashboard (recommended)

```bash
# Sample data (12 hand-curated companies, no internet needed)
python dashboard.py

# Live NIFTY 50 data from Yahoo Finance (requires yfinance)
python dashboard.py --live

# Broader universe (Nifty 50 + 15 hand-picked large caps)
python dashboard.py --live --universe nifty65

# Force a fresh fetch ignoring the disk cache
python dashboard.py --live --refresh
```

Then open <http://localhost:8000> in any browser. The dashboard has four tabs:

- **Browse** — searchable, sortable table of every company with score, gap, and recommendation. Click any row for a full report in a side drawer.
- **Top Picks** — ranked cards of the most undervalued names with score gauges.
- **Compare** — type symbols (e.g. `INFY`, press Enter, then `TCS`) to build a side-by-side comparison.
- **About** — explanation of how the score is built.

The header shows a **data-source badge** (`SAMPLE` or `LIVE · nifty50 · 49 stocks`). In live mode, **clicking the badge re-fetches all symbols from Yahoo Finance** and refreshes every view.

The web layer itself uses **only the Python standard library** (`http.server`) and vanilla HTML/CSS/JS — no Flask, no CDN, no build step.

### Command-line interface

The CLI mirrors the dashboard's data sources via the same `--live` / `--data` flags.

```bash
# ----- Sample data mode -----
# 1. List all companies in the dataset
python main.py list

# 2. Detailed report for one company
python main.py analyze TATAMOTORS

# 3. Side-by-side comparison
python main.py compare INFY TCS HDFCBANK

# 4. Top 5 undervalued companies
python main.py top -n 5
```

To use your own data file:

```bash
python main.py --data path/to/my_companies.json analyze INFY
```

```bash
# ----- Live mode (Yahoo Finance via yfinance) -----
# Single company — fetches just that symbol on demand
python main.py --live analyze INFY

# Top picks across the NIFTY 50 universe
python main.py --live top -n 10

# Compare any NSE-listed names side by side
python main.py --live compare INFY TCS HDFCBANK RELIANCE

# Force-refetch every cached symbol
python main.py --live refresh
```

Live data is cached in `data/cache/<SYMBOL>.json` for **24 hours** so subsequent runs are instant. Each symbol's cache is independent — partial failures don't corrupt the dataset.

---

## Sample output

```
================================================================
  Tata Motors  (TATAMOTORS)    Sector: EV
================================================================
  Current Price       : Rs 950.00
  Intrinsic Value     : Rs 4,471.44
  Intrinsic Range     : Rs 3,352.57  -  Rs 6,391.62
  Valuation Gap       : +78.8% (undervalued)
  Final Score         : 77.0 / 100
  Recommendation      : >>>  Strong Buy  <<<

  Model breakdown:
  ------------------------------------------------------------
  Value Investing          66.7/100  (w=25%)
     ROE 32.0% (target >15.0%), Growth 22.0% (target >10.0%), D/E 1.30 (target <0.5)
  GARP (Lynch)             98.2/100  (w=20%)
     PEG 0.55 (target <1.0); PE 12.0 / Growth 22.0%
  Deep Value (Graham)      33.3/100  (w=20%)
     PE 12.0 (<15.0), PB 4.80 (<1.5), PE*PB 57.6 (<22.5)
  DCF                     100.0/100  (w=25%)
     Intrinsic Rs 4471.44 vs Price Rs 950.00 (+78.8% margin)
  Sector Strength          90.0/100  (w=10%)
     EV: structurally growing sector
================================================================
```

---

## Input data format

Each company in `sample_companies.json` is an object with the
following fields. Optional fields can be omitted.

| Field              | Type    | Required | Notes                                  |
|--------------------|---------|----------|----------------------------------------|
| name               | string  | yes      | Display name                           |
| symbol             | string  | yes      | NSE/BSE ticker                         |
| revenue            | number  | yes      | Annual revenue (any consistent unit)   |
| profit             | number  | yes      | Net profit                             |
| debt               | number  | yes      | Total debt                             |
| equity             | number  | yes      | Shareholders' equity                   |
| pe_ratio           | number  | yes      | Price / earnings                       |
| pb_ratio           | number  | yes      | Price / book                           |
| roe                | number  | yes      | Return on equity, percent (e.g. 18.5)  |
| growth_rate        | number  | yes      | Earnings growth %, e.g. 12             |
| current_price      | number  | yes      | Latest market price per share          |
| cash_flow          | number  | no       | Free cash flow per share               |
| shares_outstanding | number  | no       | Used for EPS fallback when FCF missing |
| sector             | string  | no       | E.g. "IT", "Pharma", "EV"              |

---

## Recommendation logic

```
Strong Buy   :  score >= 75  AND  gap >= +25%
Buy          :  score >= 60  AND  gap >=   0%
Hold         :  score >= 45
Avoid        :  otherwise
```

A great-looking business that is fully priced does **not** qualify as
a Buy — both quality (score) and price (gap) need to align.

---

## Extending the system

Add a new valuation model in three steps:

1. Create `stock_valuation/valuation/my_model.py`:

   ```python
   from ..models import Company, ModelScore

   class MyModel:
       name = "My Model"

       def evaluate(self, c: Company) -> ModelScore:
           score = ...  # 0..100
           return ModelScore(self.name, score, weight=0.0,
                             notes="why this score")
   ```

2. Export it from `stock_valuation/valuation/__init__.py`.

3. Register it in `stock_valuation/scoring.py` `DEFAULT_MODELS` and
   make sure all weights still sum to **1.0**.

The analyzer, CLI, and scoring engine pick it up automatically — no
other changes needed.

---

## Using the API directly (without the CLI)

```python
from stock_valuation import Company, StockAnalyzer

infy = Company(
    name="Infosys", symbol="INFY", sector="IT",
    revenue=153670, profit=24108, debt=7900, equity=75300,
    pe_ratio=24.5, pb_ratio=7.8, roe=31.8, growth_rate=12.0,
    current_price=1480.0, cash_flow=60.5, shares_outstanding=4150,
)

analyzer = StockAnalyzer()
result = analyzer.analyze(infy)
print(result.to_dict())
```

---

## Notes & limitations

- The DCF model caps growth at 25% — perpetual >25% growth is almost
  always over-extrapolation.
- Sector classification uses curated lists (`GROWING_SECTORS` /
  `DECLINING_SECTORS` in `models.py`). Edit them to suit your view.
- **Live data** is sourced from Yahoo Finance via `yfinance`. Yahoo's
  Indian-stock coverage is generally good for large caps but has
  occasional gaps (some banks return ROE = 0, some symbols 404
  during ticker reorganizations). The system drops missing rows
  gracefully and keeps going.
- Yahoo returns per-share values (price, EPS, book) in the listing
  currency (INR) but balance-sheet totals (debt, FCF) in the
  company's reporting currency, which is sometimes USD. The data
  source layer in `stock_valuation/data_source.py` handles this by
  using **per-share fields exclusively** and deriving debt from the
  `debtToEquity` ratio so units stay consistent.
- The included `nifty50` / `nifty65` universes are hardcoded NSE
  ticker lists. To analyze a custom universe, edit
  `stock_valuation/universe.py` and add a new entry to `UNIVERSES`.
- This is an analytical tool, **not investment advice**. Always do
  your own research before buying or selling securities.
