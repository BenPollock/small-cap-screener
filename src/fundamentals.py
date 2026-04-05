"""Fundamentals fetching and quality filtering via yfinance.

Adapted from claude-backtester/src/backtester/data/sources/yahoo.py for
retry/throttle patterns.

Fetches: revenue TTM, YoY revenue growth, operating margin, debt-to-equity,
free cash flow, P/E ratio. Caches to parquet for same-day re-runs.

Designed with adapter pattern: swap yfinance for FMP/EODHD later by
implementing the same interface.
"""

import logging
import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)


def enrich_fundamentals(
    universe: pd.DataFrame,
    cache_dir: str = "./data/cache",
    max_workers: int = 8,
) -> pd.DataFrame:
    """Enrich universe DataFrame with fundamental data.

    Adds columns: revenue_ttm, revenue_growth_yoy, operating_margin,
    debt_to_equity, free_cash_flow, pe_ratio, last_fiscal_date

    Uses ThreadPoolExecutor for concurrent fetching. 8 workers keeps
    throughput well under yfinance's ~2000 req/hr limit.

    Args:
        universe: DataFrame with at least a 'ticker' column.
        cache_dir: Directory for caching.
        max_workers: Number of concurrent fetch threads.

    Returns:
        Universe DataFrame enriched with fundamental columns.
    """
    cache_path = Path(cache_dir) / f"fundamentals_{date.today().isoformat()}.parquet"
    if cache_path.exists():
        logger.info("Loading cached fundamentals from %s", cache_path)
        cached = pd.read_parquet(cache_path)
        return universe.merge(cached, on="ticker", how="left")

    partial_path = Path(cache_dir) / f"_fundamentals_partial_{date.today().isoformat()}.parquet"
    cache_path.parent.mkdir(parents=True, exist_ok=True)

    # Resume from partial checkpoint if available
    done_tickers: set[str] = set()
    fundamentals: list[dict] = []
    if partial_path.exists():
        partial_df = pd.read_parquet(partial_path)
        fundamentals = partial_df.to_dict("records")
        done_tickers = set(partial_df["ticker"])
        logger.info("Resuming fundamentals: %d tickers already completed", len(done_tickers))

    remaining = [t for t in universe["ticker"].tolist() if t not in done_tickers]
    total = len(remaining) + len(done_tickers)
    completed = len(done_tickers)
    new_since_checkpoint = 0

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_fetch_fundamentals, t): t for t in remaining}
        for future in as_completed(futures):
            ticker = futures[future]
            completed += 1
            if completed % 50 == 0:
                logger.info("Fundamentals progress: %d/%d", completed, total)
            try:
                data = future.result()
                if data is not None:
                    fundamentals.append(data)
                else:
                    fundamentals.append({"ticker": ticker})
            except Exception:
                fundamentals.append({"ticker": ticker})
            new_since_checkpoint += 1

            # Periodic checkpoint
            if new_since_checkpoint >= 20:
                pd.DataFrame(fundamentals).to_parquet(partial_path, index=False)
                new_since_checkpoint = 0

    fund_df = pd.DataFrame(fundamentals)

    # Final cache + cleanup
    fund_df.to_parquet(cache_path, index=False)
    if partial_path.exists():
        partial_path.unlink()

    return universe.merge(fund_df, on="ticker", how="left")


def _clean_numeric(value):
    """Convert non-finite or non-numeric values to None.

    yfinance can return strings like 'Infinity' or float('inf') for fields
    such as trailingPE, which causes pyarrow serialization failures.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return None
    try:
        if math.isinf(value) or math.isnan(value):
            return None
    except TypeError:
        return None
    return value


def _fetch_fundamentals(ticker: str, max_retries: int = 2) -> dict | None:
    """Fetch fundamental data for a single ticker.

    Returns dict with: ticker, revenue_ttm, revenue_growth_yoy,
    operating_margin, debt_to_equity, free_cash_flow, pe_ratio,
    last_fiscal_date, operating_cash_flow
    """
    for attempt in range(max_retries):
        try:
            t = yf.Ticker(ticker)
            info = t.info

            if not info:
                return None

            # Revenue TTM
            revenue_ttm = info.get("totalRevenue")

            # Revenue growth YoY
            revenue_growth = info.get("revenueGrowth")  # already a decimal

            # Operating margin
            operating_margin = info.get("operatingMargins")

            # Debt to equity
            debt_to_equity = info.get("debtToEquity")
            if debt_to_equity is not None:
                debt_to_equity = debt_to_equity / 100.0  # yfinance returns as percentage

            # Free cash flow
            free_cash_flow = info.get("freeCashflow")

            # P/E ratio
            pe_ratio = info.get("trailingPE")

            # Operating cash flow
            operating_cf = info.get("operatingCashflow")

            # Last fiscal date
            last_fiscal = info.get("mostRecentQuarter")
            if last_fiscal:
                last_fiscal = date.fromtimestamp(last_fiscal) if isinstance(last_fiscal, (int, float)) else None

            return {
                "ticker": ticker,
                "revenue_ttm": _clean_numeric(revenue_ttm),
                "revenue_growth_yoy": _clean_numeric(revenue_growth),
                "operating_margin": _clean_numeric(operating_margin),
                "debt_to_equity": _clean_numeric(debt_to_equity),
                "free_cash_flow": _clean_numeric(free_cash_flow),
                "pe_ratio": _clean_numeric(pe_ratio),
                "operating_cash_flow": _clean_numeric(operating_cf),
                "last_fiscal_date": last_fiscal,
            }

        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(1.0 * (attempt + 1))
            else:
                logger.debug("Failed fundamentals for %s: %s", ticker, e)
                return None

    return None


def apply_quality_filters(df: pd.DataFrame) -> pd.DataFrame:
    """Apply quality filters to the enriched universe.

    Filters:
    - Revenue growth > 0% YoY
    - Positive operating cash flow in latest quarter
    - Debt-to-equity < 2.0
    - Has reported financials in the last 6 months

    Logs survivors at each stage. Relaxes thresholds if < 100 survivors.
    """
    if df.empty:
        return df

    initial = len(df)

    # Revenue growth > 0%
    prev = len(df)
    mask_growth = df["revenue_growth_yoy"].notna() & (df["revenue_growth_yoy"] > 0)
    grown = df[mask_growth].copy()
    logger.info("After revenue growth filter (>0%%): %d/%d", len(grown), prev)

    # If too aggressive, relax
    if len(grown) < 100 and len(df) > 100:
        logger.warning("Revenue growth filter too aggressive, relaxing to >-10%%")
        mask_growth = df["revenue_growth_yoy"].notna() & (df["revenue_growth_yoy"] > -0.10)
        grown = df[mask_growth].copy()
        logger.info("After relaxed revenue growth filter: %d/%d", len(grown), prev)

    df = grown

    # Positive operating cash flow
    prev = len(df)
    mask_cf = df["operating_cash_flow"].notna() & (df["operating_cash_flow"] > 0)
    # Also keep tickers where we don't have cash flow data (don't penalize missing data)
    mask_cf = mask_cf | df["operating_cash_flow"].isna()
    df = df[mask_cf].copy()
    logger.info("After operating cash flow filter: %d/%d", len(df), prev)

    # Debt to equity < 2.0
    prev = len(df)
    mask_de = df["debt_to_equity"].isna() | (df["debt_to_equity"] < 2.0)
    df = df[mask_de].copy()
    logger.info("After debt-to-equity filter (<2.0): %d/%d", len(df), prev)

    # Recent financials (within 6 months)
    prev = len(df)
    if "last_fiscal_date" in df.columns:
        six_months_ago = pd.Timestamp(date.today()) - pd.DateOffset(months=6)
        mask_recent = df["last_fiscal_date"].isna() | (
            pd.to_datetime(df["last_fiscal_date"]) >= six_months_ago
        )
        df = df[mask_recent].copy()
        logger.info("After recent financials filter: %d/%d", len(df), prev)

    df.reset_index(drop=True, inplace=True)
    logger.info("Quality filter summary: %d/%d survived", len(df), initial)
    return df
