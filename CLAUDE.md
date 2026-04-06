# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Small-cap stock screener — a CLI-based screening pipeline that fetches current data from yfinance and SEC EDGAR, filters by market cap/volume/quality, scores on momentum + quality + insider buying signals, and outputs a ranked table of candidates for manual research. NOT a backtester — validates forward via paper portfolio tracking and ETF factor checks.

## Install & Run

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

screener run                          # Full pipeline, top 30
screener run --top 50 --skip-edgar    # Fast run without EDGAR
screener run --output csv             # CSV output
screener portfolio log --top 10       # Log picks to paper portfolio
screener portfolio show               # Track performance vs SPY
screener validate                     # ETF factor validation
```

## Run Tests

```bash
pytest                    # All tests
pytest tests/unit/        # Unit tests only
pytest tests/e2e/         # E2E tests only
pytest -k "test_momentum" # Pattern match
```

## Architecture

Data flows through the pipeline as: **universe → fundamentals → momentum → insider → scorer → output**

All stages run sequentially. EDGAR uses a token-bucket rate limiter (10 req/sec). Universe enrichment writes progressive checkpoints for crash resilience.

| Module | Responsibility |
|--------|---------------|
| `src/universe.py` | Fetch US equities from SEC EDGAR tickers endpoint, exchange pre-filter, batch volume prescreen via `yf.download()`, sequential `yf.Ticker.info` enrichment with progressive checkpoints, filter by market cap ($200M-$2B), volume (>$500K/day), exchange (NYSE/NASDAQ), exclude SPACs/ADRs/REITs |
| `src/fundamentals.py` | Fetch revenue, margins, D/E, FCF via yfinance `.info`. Apply quality filters |
| `src/momentum.py` | 6-month and 1-month ROC, relative strength vs sector ETFs, reversal penalty |
| `src/insider.py` | Orchestrates EDGAR Form 4 insider buying score computation (rate-limited) |
| `edgar/fetcher.py` | EDGAR API client — Form 4 fetching via edgartools. Adapted from claude-backtester |
| `edgar/insider_parser.py` | Form 4 parsing: purchase filtering, dollar value scoring, CEO/CFO 2x weighting |
| `edgar/rate_limiter.py` | Token-bucket rate limiter (10 req/sec) + retry with exponential backoff |
| `src/scorer.py` | Percentile rank normalization + weighted composite scoring |
| `src/output.py` | Rich terminal table, CSV, or markdown output. Auto-saves JSON |
| `src/portfolio.py` | Paper portfolio: log picks, track returns vs SPY |
| `src/validate.py` | ETF factor validation |
| `src/pipeline.py` | Orchestrates the full sequential flow |
| `src/cli.py` | Click CLI entry point |

## Key Design Decisions

- **SEC EDGAR company tickers** as universe source (comprehensive, free, no auth)
- **edgartools library** for EDGAR access — adapted from claude-backtester
- **yfinance first**, adapter pattern for future FMP/EODHD swap
- **Cache everything** to `data/cache/` as parquet/JSON; universe enrichment uses progressive checkpoints for crash resilience
- **Sequential fetching** throughout — avoids OS-level thread/DNS exhaustion on macOS
- **Token-bucket rate limiter** for SEC EDGAR fetches (10 req/sec)
- **Jegadeesh-Titman momentum** with short-term reversal filter
- **Quality filters auto-relax** through progressive tiers if < 100 tickers survive all filters combined

## Data Sources & Constraints

- **yfinance**: Free, no auth. Rate limits ~2000 req/hour. Retry + skip on failure.
- **SEC EDGAR**: 10 req/sec max, User-Agent required. Retry on 403/429.
- **Caching**: All data cached by date in `data/cache/`. Universe enrichment uses progressive checkpoints (`_*_partial_*`) for crash recovery.
