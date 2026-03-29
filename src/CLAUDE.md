# src/ Module Context

## Responsibility
Core screening pipeline modules. Each module handles one stage of the data flow: universe → fundamentals → momentum → insider → scorer → output.

## Key Functions & Contracts

### universe.py
- `get_universe(min_mcap, max_mcap, cache_dir) → DataFrame` — Returns filtered universe with columns: ticker, company_name, market_cap, avg_volume, avg_dollar_volume, sector, industry, exchange
- `_apply_filters(df, min_mcap, max_mcap, include_reits) → DataFrame` — Applies all filters with logging at each stage

### fundamentals.py
- `enrich_fundamentals(universe, cache_dir) → DataFrame` — Adds: revenue_ttm, revenue_growth_yoy, operating_margin, debt_to_equity, free_cash_flow, pe_ratio, operating_cash_flow, last_fiscal_date
- `apply_quality_filters(df) → DataFrame` — Filters on growth, OCF, D/E, recency. Auto-relaxes if too aggressive.

### momentum.py
- `compute_momentum_scores(df, cache_dir) → DataFrame` — Adds: roc_6m, roc_1m, sector_roc_6m, relative_strength, momentum_score
- Short-term reversal penalty: top 10% 1m ROC → 50% score reduction

### insider.py
- `compute_insider_scores(df, cache_dir) → DataFrame` — Adds: insider_score (0 = no buying)
- Uses EdgarClient from edgar/ module

### scorer.py
- `compute_composite_scores(df, top, skip_edgar) → DataFrame` — Percentile ranks + weighted composite. Adds: momentum_rank, quality_score, quality_rank, insider_rank, composite_score, rank
- Weights: 40/30/30 (with EDGAR) or 55/45/0 (without)

### output.py
- `render_output(df, output_format)` — Renders terminal/CSV/markdown
- `save_screen(df, screens_dir) → Path` — Always saves JSON to data/screens/

### portfolio.py
- `log_portfolio(top, cache_dir)` — Runs pipeline, records entry prices to data/portfolio/portfolio.json
- `show_portfolio()` — Shows all cohort returns vs SPY

### validate.py
- `run_validation(period) → DataFrame` — Compares factor ETFs vs benchmarks (CAGR, Sharpe, MaxDD)

## Gotchas
- yfinance `.info` returns `debtToEquity` as a percentage (e.g., 50.0 for 0.5x) — we convert to ratio
- `mostRecentQuarter` from yfinance is a Unix timestamp, not a date
- Quality filters auto-relax threshold from >0% to >-10% growth if < 100 tickers survive
- All modules cache by date — same-day re-runs are instant
