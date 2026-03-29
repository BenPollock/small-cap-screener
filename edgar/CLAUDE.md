# edgar/ Module Context

## Responsibility
SEC EDGAR API client for Form 4 insider trading data. Adapted from claude-backtester's EDGAR modules.

## Key Components

### fetcher.py — EdgarClient
- `EdgarClient(user_agent, max_filings)` — EDGAR API client using edgartools library
- `fetch_form4(symbol) → DataFrame` — Returns insider transactions with columns: filed_date, transaction_date, insider_name, insider_title, transaction_type (P/S), shares, price, shares_after, is_direct
- Handles both DataFrame-based (newer) and iterable-based (legacy) edgartools APIs
- Bug fixes ported from backtester: CIK issuer verification (Bug 1), multi-API transaction extraction (Bug 2), zero-price transaction filtering (Bug 3)

### insider_parser.py
- `parse_insider_transactions(df) → DataFrame` — Filters to purchases only (type "P"), adds dollar_value column
- `score_insider_buying(transactions, cutoff_date) → float` — Scores based on: unique insiders (0-40pts, log scale), dollar value (0-40pts, log scale), executive weighting (0-20pts, CEO/CFO 2x)

### rate_limiter.py
- `RateLimiter(max_per_second)` — Token bucket rate limiter, default 10 req/sec
- `edgar_retry(max_retries, initial_backoff)` — Decorator for exponential backoff on 403/429/rate limit errors
- `is_rate_limit_error(exc)` — Checks exception for rate limit indicators

## EDGAR Specifics
- **Rate limit**: 10 req/sec enforced by RateLimiter + edgartools internal limiting
- **User-Agent**: Required by SEC. Set via `set_identity()` in edgartools
- **Retry**: Exponential backoff 10s→20s→40s on 403/429. Max 3 retries.
- **Form 4 transaction codes**: P = purchase, S = sale. We only score purchases.
- **CIK matching**: Verify filing issuer CIK matches target company to avoid institutional investor filings

## Gotchas
- edgartools column names vary by version — `_resolve_col()` tries multiple candidates
- Some Form 4 filings have the company as filer (not issuer) — CIK check filters these
- Zero-price transactions are grants/awards, not real purchases — skip them
