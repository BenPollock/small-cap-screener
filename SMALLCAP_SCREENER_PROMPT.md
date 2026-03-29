# Small-Cap Stock Screener — Claude Code Build Prompt

## What This Is

A CLI-based small-cap stock screening pipeline. NOT a backtester. It fetches current data, filters, scores, ranks, and outputs a table of candidates for manual research. Think of it as a systematic funnel — not a trading system.

**Core thesis:** Market efficiency scales with coverage. Stocks under $2B market cap with low analyst coverage are genuinely less efficient. Known factor premia (momentum, quality, insider buying) are strongest in this segment. We can't backtest this properly without expensive data, but we CAN build a disciplined forward screening process and validate the underlying factors via ETF proxies.

## Upstream Repo: Reuse Existing Code

**CRITICAL: Do NOT rewrite yfinance or EDGAR logic from scratch.** There is an existing backtester at `https://github.com/BenPollock/claude-backtester` with working, tested code for:

- **yfinance data fetching** — price history, fundamentals, caching patterns
- **EDGAR Form 4 parsing** — `src/backtester/data/edgar_insider.py`
- **EDGAR API client** — `src/backtester/data/edgar_source.py` (rate limiting, User-Agent, endpoints)
- **EDGAR institutional data** — `src/backtester/data/edgar_institutional.py`
- **EDGAR 8-K events** — `src/backtester/data/edgar_events.py`

**Before writing any data-fetching code:**
1. Clone the backtester repo to a temp directory
2. Read the relevant source files listed above
3. Extract and adapt the working logic into this project's modules — refactor for the screener's needs, but preserve the battle-tested fetching, parsing, and rate-limiting code
4. Document in each module's docstring: "Adapted from claude-backtester/{original_file}" so we can trace provenance

Do NOT copy the backtester's strategy engine, position management, or simulation logic — that's irrelevant here. Only the data layer.

## Architecture

```
smallcap-screener/
├── .claude/
│   └── CLAUDE.md            # Root project context (see CLAUDE.md Protocol below)
├── src/
│   ├── CLAUDE.md            # Module-level context for src/
│   ├── cli.py               # Click-based CLI entry point
│   ├── pipeline.py          # Orchestrates: universe → filter → score → rank → output
│   ├── universe.py          # Fetches all US equities, applies market cap + volume filters
│   ├── fundamentals.py      # Fetches financial data (yfinance first, FMP adapter later)
│   ├── momentum.py          # Price momentum calculations (ROC, relative strength)
│   ├── insider.py           # EDGAR Form 4 insider buying signals (adapted from backtester)
│   ├── scorer.py            # Composite scoring: weights × normalized factor scores
│   ├── portfolio.py         # Paper portfolio tracker: log picks, track performance
│   └── output.py            # Renders results as markdown table, CSV, or terminal
├── edgar/
│   ├── CLAUDE.md            # EDGAR module context: endpoints, rate limits, data formats
│   ├── fetcher.py           # EDGAR API client (adapted from backtester's edgar_source.py)
│   ├── insider_parser.py    # Form 4 parsing (adapted from backtester's edgar_insider.py)
│   └── rate_limiter.py      # SEC rate limiting (10 req/sec, User-Agent required)
├── tests/
│   ├── CLAUDE.md            # Testing conventions, fixture locations, mocking rules
│   ├── conftest.py          # Shared fixtures: mock yfinance responses, mock EDGAR XML, sample DataFrames
│   ├── fixtures/            # Static test data (sample Form 4 XML, yfinance JSON responses, etc.)
│   ├── unit/
│   │   ├── test_universe.py
│   │   ├── test_fundamentals.py
│   │   ├── test_momentum.py
│   │   ├── test_insider.py
│   │   ├── test_scorer.py
│   │   ├── test_portfolio.py
│   │   └── test_output.py
│   └── e2e/
│       ├── test_full_pipeline.py    # Runs full pipeline with mocked HTTP layer
│       ├── test_cli_commands.py     # Tests all CLI commands via Click's CliRunner
│       └── test_portfolio_flow.py   # Log → show round-trip with mocked prices
├── data/
│   ├── screens/             # Saved screen results (timestamped JSON)
│   └── portfolio/           # Paper portfolio state (JSON)
├── pyproject.toml
└── README.md
```

## CLAUDE.md Protocol

**CRITICAL: Keep CLAUDE.md files updated throughout development.** These are the single most important files for session efficiency — they save thousands of tokens on project re-reads.

### Root `.claude/CLAUDE.md`
Must contain:
- One-paragraph project summary
- How to install and run (`pip install -e .`, `screener run`, etc.)
- How to run tests (`pytest`, `pytest tests/e2e/`)
- Architecture overview: which module does what, data flow through the pipeline
- Key design decisions and why they were made
- What data sources are used (yfinance, EDGAR) and their constraints (rate limits, caching)
- What was adapted from the upstream backtester repo and where

### Module-level `CLAUDE.md` files (in `src/`, `edgar/`, `tests/`)
Must contain:
- What this module is responsible for
- Key classes/functions and their contracts (inputs → outputs)
- Gotchas, edge cases, known limitations
- For `tests/`: mocking conventions, how fixtures work, how to add a new test

### Update rules:
- **Every time a module's public API changes**, update its CLAUDE.md
- **Every time a key design decision is made**, add it to root CLAUDE.md
- **Every agent must update relevant CLAUDE.md files as their LAST action** before writing checkpoints
- CLAUDE.md files should be concise — not exhaustive docs, but enough that a new session can understand the codebase without reading every source file

---

## Testing Strategy

**Tests are first-class outputs, not afterthoughts.** Every module gets unit tests. The full pipeline gets E2E tests. Tests are written alongside implementation, not bolted on at the end.

### Mocking Philosophy: Mock the Network, Not the Logic
- **DO mock:** HTTP requests to yfinance, HTTP requests to EDGAR, file system for cache reads/writes
- **DO NOT mock:** DataFrame transformations, scoring logic, ranking algorithms, filter logic, CLI argument parsing
- The goal is to test that real data flows through real logic and produces correct results. The only fake thing should be where the data comes from.

### Fixtures
Store realistic sample data in `tests/fixtures/`:
- `sample_form4.xml` — a real Form 4 filing (anonymized if needed)
- `sample_yfinance_info.json` — a real yfinance `.info` response for 2-3 tickers
- `sample_price_history.csv` — real price data for a small set of tickers
- These should be captured from actual API responses, not hand-crafted. This ensures tests catch real parsing edge cases.

### Unit Tests (per module)
Each module in `src/` and `edgar/` gets a corresponding test file. Tests should cover:
- Happy path with realistic data
- Edge cases: missing fields, NaN values, zero volume, negative earnings
- Filter boundary conditions (ticker exactly at $200M market cap — in or out?)
- Scoring normalization (what happens when all tickers have the same momentum?)

### E2E Tests
- `test_full_pipeline.py` — mock HTTP layer (use `responses` or `pytest-httpserver`), run full `screener run`, verify output format and ranking correctness
- `test_cli_commands.py` — use Click's `CliRunner` to test every CLI command with every flag combination. Verify exit codes, output format, error messages.
- `test_portfolio_flow.py` — log a cohort, then show it, verify the round-trip produces correct returns math

### Test Commands
```bash
pytest                       # Run all tests
pytest tests/unit/           # Unit tests only
pytest tests/e2e/            # E2E tests only
pytest -x --tb=short         # Stop on first failure, short tracebacks
pytest -k "test_momentum"    # Run tests matching pattern
```

### Agent Testing Rule
**Every agent must have passing tests for their module before writing their checkpoint.** If tests fail, fix the code, don't skip the tests. A module without tests is not complete.

---

## Checkpoint / Resume System

**CRITICAL: We are on a limited Claude plan. Sessions may run out mid-build.**

Every agent must write progress to checkpoint files so the next session can resume without re-reading everything or redoing completed work.

### Checkpoint Protocol

1. Each agent writes status to `/tmp/screener_checkpoint_{agent_name}.md` after completing each task
2. Checkpoint format:
```markdown
# Agent: {name}
## Last Updated: {timestamp}

## Completed
- [x] Task 1: description — DONE
- [x] Task 2: description — DONE

## In Progress
- [ ] Task 3: description — started, file X created but not tested

## Not Started
- [ ] Task 4: description

## Key Decisions Made
- Chose X over Y because Z
- Found bug in W, fixed by doing V

## Files Created/Modified
- src/universe.py — complete, tested
- src/momentum.py — created, needs unit tests

## Blockers / Questions for Next Session
- Need to decide on FMP vs EODHD for fundamentals
- yfinance rate limiting hit at 500 tickers — may need batching
```

3. A master checkpoint at `/tmp/screener_checkpoint_MASTER.md` aggregates all agent statuses
4. **First action of any new session:** read all checkpoint files in `/tmp/` before doing anything

---

## Phase 0: Project Setup (Sequential — do this first)

1. Clone the upstream backtester for reference: `git clone https://github.com/BenPollock/claude-backtester /tmp/claude-backtester`
2. Read the backtester's EDGAR modules: `edgar_source.py`, `edgar_insider.py`, `edgar_institutional.py`, `edgar_events.py`, and its yfinance data fetching code. Take notes on what to reuse.
3. Initialize the project structure above
4. Set up `pyproject.toml` with dependencies: `click`, `yfinance`, `pandas`, `requests`, `rich` (for terminal tables), `pytest`, `responses` (for HTTP mocking in tests)
5. Create the root `.claude/CLAUDE.md` with initial project summary and architecture
6. Create a minimal CLI skeleton:
```bash
screener run              # Run full pipeline, output top 30
screener run --top 50     # Output top 50
screener run --output csv # Output as CSV
screener portfolio log    # Log today's top picks to paper portfolio
screener portfolio show   # Show paper portfolio performance
screener validate         # Run ETF factor validation (see Agent 3)
```
7. Write checkpoint: `/tmp/screener_checkpoint_setup.md`

---

## Agent 1: Universe & Fundamentals

**Goal:** Build the data layer that fetches and filters the investable universe.

### Task 1: Universe Construction
Build `universe.py` that:
- Pulls all US-listed equities from yfinance (use the `Screener` module or a pre-built ticker list)
- Filters to: market cap $200M–$2B, average daily dollar volume > $500K, listed on NYSE/NASDAQ
- Excludes: ADRs, SPACs, REITs (optional flag), biotech pre-revenue (operating income < 0 for 4 consecutive quarters)
- Outputs a DataFrame with: ticker, market_cap, avg_volume, sector, industry

**Known challenge:** yfinance doesn't have a reliable "get all US tickers" endpoint. Options:
- Use `yfinance.Screener` if it supports market cap range filters
- Fetch a pre-built list from NASDAQ's FTP site (nasdaqtrader.com) or SEC's EDGAR company tickers endpoint
- As fallback: start with Russell 2000 constituents (available free) as the initial universe

Document which approach you chose and why. If yfinance rate-limits, implement batching with delays.

### Task 2: Fundamentals Fetching
Build `fundamentals.py` that for each ticker in the universe fetches:
- Revenue TTM and YoY growth
- Operating margin
- Debt-to-equity
- Free cash flow (positive/negative)
- P/E ratio

Use yfinance `.info` and `.quarterly_financials`. Cache aggressively — write fetched data to `data/cache/fundamentals_{date}.parquet` so re-runs don't re-fetch.

### Task 3: Quality Filter
Apply filters to the universe:
- Revenue growth > 0% YoY (not necessarily 10% — be less aggressive initially)
- Positive operating cash flow in latest quarter
- Debt-to-equity < 2.0
- Has reported financials in the last 6 months (screens out shell companies)

Log how many tickers survive each filter stage. If too aggressive (< 100 survivors), relax thresholds.

### Task 4: Tests
Write unit tests for `universe.py` and `fundamentals.py`:
- Mock yfinance HTTP calls using `responses` library with fixtures from real API responses
- Test filter logic with edge cases (ticker exactly at market cap boundary, missing fundamentals, NaN values)
- Test caching: verify cache writes on first call, cache reads on second call
- All tests must pass before proceeding.

### Task 5: Documentation & Checkpoint
- Update `src/CLAUDE.md` with universe and fundamentals module contracts
- Update root `.claude/CLAUDE.md` with any design decisions made
- Write `/tmp/screener_checkpoint_agent1.md` with:
  - How many tickers in raw universe
  - How many survive each filter
  - Any yfinance issues encountered
  - Cache file locations

---

## Agent 2: Signals & Scoring

**Goal:** Build the momentum, insider, and composite scoring modules.

### Task 1: Momentum Signal
Build `momentum.py`:
- 6-month price momentum (126 trading day ROC)
- 1-month momentum (21 trading day ROC) — used as a short-term mean reversion filter
- Relative strength vs sector (stock momentum minus sector ETF momentum)

For price data: use yfinance `.history()` with period="1y". Cache to parquet.

**Composite momentum score:**
- Primary: 6-month ROC (higher = better)
- Penalty: if 1-month ROC is in top 10% (too extended, mean reversion risk), reduce score by 50%
- This is the standard Jegadeesh-Titman momentum with a short-term reversal filter

### Task 2: Insider Signal
Adapt the EDGAR insider buying logic from the backtester repo (cloned in Phase 0 to `/tmp/claude-backtester`). Build `insider.py` and `edgar/`:
- Read `/tmp/claude-backtester/src/backtester/data/edgar_insider.py` and `edgar_source.py` thoroughly
- Extract the Form 4 fetching, parsing, and rate-limiting logic
- Refactor for screener use: we need a function that takes a list of tickers and returns insider buying scores, not a streaming data feed for backtesting
- Filter to PURCHASES only (transaction code "P"). Ignore sales, grants, exercises.
- Score based on:
  - Number of unique insiders buying in last 90 days
  - Total dollar value of purchases in last 90 days
  - CEO/CFO purchases weighted 2x vs director purchases

**EDGAR specifics:**
- Rate limit: 10 requests/second, must include User-Agent with contact email
- Endpoint: `https://efts.sec.gov/LATEST/search-index?q=...` or full-text search
- Form 4 filings are XML — parse transaction codes and amounts
- The backtester has working code for this in `edgar_insider.py` and `edgar_source.py` — read those first and port the relevant logic. Don't rewrite from scratch.

**Important:** EDGAR fetching for 500+ tickers will be slow (rate limited). Implement:
- Parallel fetching up to rate limit
- Cache results to `data/cache/insider_{date}.json`
- A `--skip-edgar` flag to run without insider data (momentum + fundamentals only)

### Task 3: Composite Scorer
Build `scorer.py`:
- Normalize each signal to 0-100 percentile rank within the universe
- Composite score = weighted sum:
  - 40% momentum score
  - 30% quality score (composite of: revenue growth rank + margin rank + FCF rank)
  - 30% insider score (0 if no insider buying, scaled by activity level)
  - If `--skip-edgar`: 55% momentum, 45% quality
- Rank by composite score descending
- Output top N (default 30)

### Task 4: Tests
Write unit tests for `momentum.py`, `insider.py`, `scorer.py`, and `edgar/`:
- Test momentum calculations against hand-computed values on fixture price data
- Test Form 4 XML parsing against real fixture data (save a sample Form 4 XML to `tests/fixtures/`)
- Test scorer normalization edge cases: all same values, single ticker, NaN scores
- Test composite scoring with and without insider data (`--skip-edgar` mode)
- Mock only HTTP calls — all DataFrame logic must run for real
- All tests must pass before proceeding.

### Task 5: Documentation & Checkpoint
- Update `src/CLAUDE.md` and `edgar/CLAUDE.md` with module contracts and EDGAR specifics
- Write `/tmp/screener_checkpoint_agent2.md`

---

## Agent 3: Output, Portfolio Tracking & Factor Validation

**Goal:** Build the output rendering, paper portfolio system, and ETF-based factor validation.

### Task 1: Output Rendering
Build `output.py`:
- Terminal mode: Rich table with columns: Rank, Ticker, Sector, MktCap, 6mROC, Quality, Insider, Composite
- CSV mode: same columns to stdout or file
- Markdown mode: same as terminal but formatted as .md file saved to `data/screens/screen_{date}.md`

Every screen run automatically saves to `data/screens/` as JSON (machine-readable) regardless of output format.

### Task 2: Paper Portfolio Tracker
Build `portfolio.py`:
- `screener portfolio log` — takes today's top N picks, records: date, tickers, prices at close, composite scores
- `screener portfolio show` — for each logged cohort, fetches current prices, calculates return since selection date
- Shows: cohort date, holding period, equal-weight portfolio return, SPY return over same period, excess return
- Stores state in `data/portfolio/portfolio.json`

This is how we validate forward. No backtest needed — we just track whether the picks work starting now.

### Task 3: ETF Factor Validation
Build a `screener validate` command that runs simple factor checks on ETFs (no survivorship bias):
- Small-cap value premium: compare SLYV (S&P 600 Value) vs SLY (S&P 600) — 10yr CAGR, Sharpe
- Small-cap momentum premium: compare XSMO (S&P 600 Momentum) vs SLY
- Small-cap quality premium: compare SPHQ vs SPY (large-cap quality, as small-cap quality ETF may not exist)
- Size premium: compare IWM (Russell 2000) vs SPY
- Insider buying signal: compare any insider-buying ETF (e.g., KNOW, NFO) vs SPY if they exist with enough history

Output a clean table showing: Factor, ETF, Benchmark, Period, CAGR, Sharpe, MaxDD, Excess Return.

This doesn't prove OUR screener works, but it proves the underlying factors have real premia. If small-cap momentum shows 0 excess return over 10 years, we should reconsider the whole approach.

### Task 4: Tests
Write unit tests for `output.py` and `portfolio.py`:
- Test all three output modes (terminal, CSV, markdown) produce correct format
- Test portfolio log/show round-trip with mocked price fetches
- Test returns calculation accuracy: given known entry/current prices, verify correct % returns and excess vs SPY
- Test validate command produces correct CAGR/Sharpe calculations against known ETF data

### Task 5: Documentation & Checkpoint
- Update `src/CLAUDE.md` with output and portfolio module contracts
- Update `tests/CLAUDE.md` with testing conventions, fixture descriptions, how to add tests
- Write `/tmp/screener_checkpoint_agent3.md`

---

## Agent 4: Integration & CLI

**Goal:** Wire everything together into a working CLI.

### Task 1: Pipeline Orchestration
Build `pipeline.py` that:
1. Calls universe.py → gets filtered universe
2. Calls fundamentals.py → enriches with financial data
3. Calls momentum.py → adds momentum scores
4. Calls insider.py → adds insider scores (if not --skip-edgar)
5. Calls scorer.py → computes composite ranking
6. Calls output.py → renders result

Handle errors gracefully — if a ticker fails to fetch data, skip it and log a warning. Don't let one bad ticker crash the whole pipeline.

### Task 2: CLI Implementation
Implement the full CLI with Click:
```
screener run [--top N] [--output terminal|csv|markdown] [--skip-edgar] [--min-mcap 200] [--max-mcap 2000] [--cache-dir ./data/cache]
screener portfolio log [--top N]
screener portfolio show
screener validate [--period 10y]
```

### Task 3: E2E Tests
Write E2E tests that exercise the full system:
- `test_full_pipeline.py` — mock the HTTP layer (yfinance + EDGAR), run `screener run`, verify:
  - Output contains expected columns
  - Rankings are sorted by composite score descending
  - Ticker count matches `--top N` flag
  - JSON screen file is saved to `data/screens/`
- `test_cli_commands.py` — use Click's `CliRunner` to test EVERY command and flag:
  - `screener run` (default)
  - `screener run --top 10 --output csv --skip-edgar --min-mcap 300 --max-mcap 1500`
  - `screener portfolio log --top 5`
  - `screener portfolio show`
  - `screener validate --period 5y`
  - Invalid flags produce helpful error messages
  - Missing required state (e.g., `portfolio show` before any `portfolio log`) produces clear errors
- `test_portfolio_flow.py` — full log → show round-trip with time-shifted mock prices
- All tests must pass.

### Task 4: README
Write a comprehensive `README.md` that includes:
- **Project summary:** What this is (small-cap screener), what it's NOT (a backtester or trading system), the core thesis
- **Installation:** step-by-step from clone to first run
- **Quick start:** get a screen result in 3 commands
- **CLI Reference:** exhaustive list of ALL commands, ALL flags, with defaults and descriptions. Format:
  ```
  screener run
    --top N              Number of stocks to display (default: 30)
    --output FORMAT      Output format: terminal, csv, markdown (default: terminal)
    --skip-edgar         Skip EDGAR insider data fetching (faster, less signal)
    --min-mcap N         Minimum market cap in millions (default: 200)
    --max-mcap N         Maximum market cap in millions (default: 2000)
    --cache-dir PATH     Cache directory (default: ./data/cache)

  screener portfolio log
    --top N              Number of top picks to log (default: 10)

  screener portfolio show
    (no flags)

  screener validate
    --period PERIOD      yfinance period string (default: 10y)
  ```
- **Examples:** real usage examples for common workflows:
  - "Run a quick screen without EDGAR data"
  - "Run a full screen and log results to paper portfolio"
  - "Check how your past picks performed"
  - "Validate that factor premia exist"
  - "Export results as CSV for spreadsheet analysis"
- **Architecture:** brief description of data flow and module responsibilities
- **Data sources:** what data comes from where, rate limits, caching behavior
- **Upstream:** acknowledgment that EDGAR modules adapted from claude-backtester

### Task 5: Final Documentation & Master Checkpoint
- Do a final pass on ALL `CLAUDE.md` files — ensure they reflect the actual built state, not the planned state
- Verify root `.claude/CLAUDE.md` has: project summary, install/run/test commands, architecture, all key decisions
- Write `/tmp/screener_checkpoint_MASTER.md` summarizing all agents' status
- Run `pytest` one final time — all tests must pass

---

## Hard Rules

1. **Cache everything.** EDGAR and yfinance calls are slow and rate-limited. Every fetch writes to `data/cache/`. Re-runs within the same day hit cache first.
2. **No backtesting.** This is a forward-looking screener. The portfolio tracker validates forward. The ETF validation proves factors exist historically. Don't try to simulate historical screening.
3. **yfinance first, paid API later.** Design `fundamentals.py` with an adapter pattern so we can swap in FMP/EODHD later without changing the pipeline. But v1 uses yfinance only.
4. **Checkpoints after every task.** Sessions may terminate unexpectedly. Every completed task gets logged to `/tmp/screener_checkpoint_{agent}.md`.
5. **Keep it simple.** No database, no web UI, no scheduler, no Docker. CLI that outputs text. We add complexity only after the output proves useful.
6. **Handle yfinance failures gracefully.** It WILL fail for some tickers, rate-limit you, return incomplete data. Log and skip, never crash.
7. **EDGAR rate limit is sacred.** 10 req/sec max, User-Agent header required with real email. Getting IP-banned by the SEC would be bad.
8. **Tests are not optional.** Every module gets unit tests. E2E tests cover the full pipeline. Tests mock HTTP calls only — all business logic runs for real. No module is complete without passing tests.
9. **CLAUDE.md files stay current.** Update them as you build, not as an afterthought. If you change a module's API, update its CLAUDE.md in the same task.
10. **Reuse backtester code for EDGAR and yfinance.** Read the upstream repo before writing data-fetching code. Adapt, don't reinvent.

## Resume Protocol

If starting a new session after a previous one ran out:

1. Read ALL files in `/tmp/screener_checkpoint_*.md`
2. Read all files in the project directory to understand current state
3. Pick up from where the last session left off — do NOT redo completed work
4. If a task was "in progress" when the session ended, review the partial work before continuing

## Definition of Done

The project is done when:
1. `screener run` outputs a ranked table of 30 small-cap stocks in < 5 minutes
2. `screener portfolio log` records the picks with timestamps
3. `screener portfolio show` tracks performance of logged picks vs SPY
4. `screener validate` shows factor premia (or lack thereof) on ETFs
5. `pytest` passes — all unit tests and E2E tests green
6. All `CLAUDE.md` files are up-to-date and reflect the actual built state
7. `README.md` includes project summary, installation, quick start, exhaustive CLI reference with all flags, and usage examples for every common workflow
8. All checkpoint files are written
