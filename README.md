# Small-Cap Stock Screener

A CLI-based small-cap stock screening pipeline. Fetches current data, filters, scores, ranks, and outputs a table of candidates for manual research. Think of it as a systematic funnel — not a trading system.

**What it is:** A forward-looking screener that combines momentum, quality, and insider buying signals to surface under-followed small-cap stocks.

**What it is NOT:** A backtester. There is no historical simulation. Forward performance is tracked via a paper portfolio, and the underlying factor premia are validated via ETF comparisons.

**Core thesis:** Market efficiency scales with coverage. Stocks under $2B market cap with low analyst coverage are genuinely less efficient. Known factor premia (momentum, quality, insider buying) are strongest in this segment.

## Installation

```bash
git clone https://github.com/BenPollock/small-cap-screener.git
cd small-cap-screener
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Quick Start

```bash
# Run a quick screen without EDGAR data (faster)
screener run --skip-edgar

# Run a full screen with insider data
screener run

# See your top 10 picks
screener run --top 10
```

## CLI Reference

### `screener run`

Run the full screening pipeline and output top picks.

```
screener run [OPTIONS]

Options:
  --top N              Number of stocks to display (default: 30)
  --output FORMAT      Output format: terminal, csv, markdown (default: terminal)
  --skip-edgar         Skip EDGAR insider data fetching (faster, less signal)
  --min-mcap N         Minimum market cap in millions (default: 200)
  --max-mcap N         Maximum market cap in millions (default: 2000)
  --cache-dir PATH     Cache directory (default: ./data/cache)
```

### `screener portfolio log`

Log today's top picks to the paper portfolio with entry prices.

```
screener portfolio log [OPTIONS]

Options:
  --top N              Number of top picks to log (default: 10)
  --cache-dir PATH     Cache directory (default: ./data/cache)
```

### `screener portfolio show`

Show paper portfolio performance for all logged cohorts vs SPY.

```
screener portfolio show
```

### `screener validate`

Run ETF factor validation to check that factor premia exist historically.

```
screener validate [OPTIONS]

Options:
  --period PERIOD      yfinance period string (default: 10y)
```

## Examples

### Run a quick screen without EDGAR data

```bash
screener run --skip-edgar
```

This uses only momentum + quality signals (55%/45% weighting). Much faster since it skips SEC EDGAR API calls.

### Run a full screen and log results to paper portfolio

```bash
screener run --top 20
screener portfolio log --top 20
```

### Check how your past picks performed

```bash
screener portfolio show
```

Shows each cohort's equal-weight return vs SPY over the holding period.

### Validate that factor premia exist

```bash
screener validate
screener validate --period 5y
```

Compares small-cap value (SLYV), momentum (XSMO), quality (SPHQ), and size (IWM) ETFs against benchmarks.

### Export results as CSV for spreadsheet analysis

```bash
screener run --output csv > screen_results.csv
```

### Narrow the market cap range

```bash
screener run --min-mcap 300 --max-mcap 1500 --skip-edgar
```

## Architecture

```
Universe (SEC EDGAR tickers + yfinance enrichment)
  → Filter (market cap, volume, exchange, no SPACs/ADRs/REITs)
  → Fundamentals (revenue growth, margins, D/E, FCF via yfinance)
  → Quality Filter (growth > 0%, positive OCF, D/E < 2.0)
  → Momentum (6-month ROC, 1-month ROC, sector-relative strength)
  → Insider Buying (EDGAR Form 4 purchases, CEO/CFO weighted 2x)
  → Composite Score (weighted percentile ranks)
  → Output (terminal table, CSV, markdown, auto-saved JSON)
```

### Scoring Weights

| Signal | With EDGAR | Without EDGAR |
|--------|-----------|---------------|
| Momentum | 40% | 55% |
| Quality | 30% | 45% |
| Insider | 30% | 0% |

## Data Sources

| Source | What | Rate Limits | Auth |
|--------|------|------------|------|
| yfinance | Price history, fundamentals, market cap | ~2000 req/hr | None |
| SEC EDGAR | Company tickers, Form 4 insider filings | 10 req/sec | User-Agent with email |

All fetched data is cached by date in `data/cache/`. Same-day re-runs are instant.

## Upstream

EDGAR modules (`edgar/fetcher.py`, `edgar/insider_parser.py`, `edgar/rate_limiter.py`) were adapted from [claude-backtester](https://github.com/BenPollock/claude-backtester). Specifically:

- Form 4 fetching and parsing from `src/backtester/data/edgar_insider.py`
- Rate limiting and retry logic from `src/backtester/data/edgar_utils.py`
- yfinance data fetching patterns from `src/backtester/data/sources/yahoo.py`

## Testing

```bash
pytest                       # Run all 87 tests
pytest tests/unit/           # Unit tests only
pytest tests/e2e/            # E2E tests only
pytest -x --tb=short         # Stop on first failure
pytest -k "test_momentum"    # Run tests matching pattern
```

Tests mock HTTP calls only — all business logic (DataFrame transforms, scoring, ranking) runs with real data.

## License

MIT
