# tests/ Module Context

## Responsibility
87 tests covering all modules — unit tests for each src/ and edgar/ module, E2E tests for pipeline and CLI.

## Test Structure
```
tests/
├── conftest.py          # Shared fixtures: sample DataFrames, mock data
├── fixtures/            # Static test data files
│   ├── sample_yfinance_info.json
│   └── sample_form4.xml
├── unit/
│   ├── test_universe.py      # SEC ticker fetching, all filter logic, cache
│   ├── test_fundamentals.py  # yfinance fetching, quality filters, edge cases
│   ├── test_momentum.py      # ROC calculations, reversal penalty, cache
│   ├── test_insider.py       # Form 4 parsing, scoring, executive weighting
│   ├── test_scorer.py        # Percentile ranking, composite scoring, edge cases
│   ├── test_output.py        # Terminal/CSV/markdown rendering, JSON saving
│   └── test_portfolio.py     # Storage roundtrip, price fetching, SPY return
└── e2e/
    ├── test_full_pipeline.py    # Full pipeline with all HTTP mocked
    ├── test_cli_commands.py     # Click CliRunner for all commands + flags
    └── test_portfolio_flow.py   # Log → show roundtrip with mock prices
```

## Mocking Conventions
- **Mock HTTP only**: yfinance `yf.Ticker` and EDGAR `EdgarClient` are mocked at the module level
- **Real logic runs**: All DataFrame transforms, scoring, filtering, ranking execute with real data
- **Use `@patch("src.module.yf.Ticker")`** to mock yfinance at the import site
- **Use `@responses.activate`** for raw HTTP mocking (SEC EDGAR ticker endpoint)

## Fixtures (conftest.py)
- `sample_universe` — 5 tickers with market cap, volume, sector data
- `sample_fundamentals` — Matching fundamental data
- `sample_enriched` — Universe merged with fundamentals
- `sample_momentum` — Momentum scores for 5 tickers
- `sample_insider` — Insider scores for 5 tickers
- `sample_form4_transactions` — 4 Form 4 transactions (3 buys, 1 sale)
- `sample_price_history` — 1 year of daily prices (numpy-generated)
- `sample_ranked_df` — Fully scored and ranked DataFrame
- `mock_yfinance_info` — Sample .info responses from fixtures file

## How to Add a New Test
1. Add fixture data to `tests/fixtures/` if needed
2. Add shared fixtures to `conftest.py`
3. Create test class in the appropriate unit/ or e2e/ file
4. Mock HTTP calls with `@patch` or `@responses.activate`
5. Let all business logic run for real
6. Run: `pytest -k "test_your_new_test" -v`
