"""Fundamentals fetching and quality filtering via yfinance.

Adapted from claude-backtester/src/backtester/data/sources/yahoo.py for
retry/throttle patterns.

Fetches: revenue TTM, YoY revenue growth, operating margin, debt-to-equity,
free cash flow, P/E ratio. Caches to parquet for same-day re-runs.

Designed with adapter pattern: swap yfinance for FMP/EODHD later by
implementing the same interface.
"""

import logging
import time
from datetime import date
from pathlib import Path

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)


def enrich_fundamentals(
    universe: pd.DataFrame,
    cache_dir: str = "./data/cache",
    batch_delay: float = 1.0,
) -> pd.DataFrame:
    """Enrich universe DataFrame with fundamental data.

    Adds columns: revenue_ttm, revenue_growth_yoy, operating_margin,
    debt_to_equity, free_cash_flow, pe_ratio, last_fiscal_date

    Args:
        universe: DataFrame with at least a 'ticker' column.
        cache_dir: Directory for caching.
        batch_delay: Delay between ticker fetches to avoid rate limiting.

    Returns:
        Universe DataFrame enriched with fundamental columns.
    """
    cache_path = Path(cache_dir) / f"fundamentals_{date.today().isoformat()}.parquet"
    if cache_path.exists():
        logger.info("Loading cached fundamentals from %s", cache_path)
        cached = pd.read_parquet(cache_path)
        return universe.merge(cached, on="ticker", how="left")

    fundamentals = []
    total = len(universe)

    for idx, row in universe.iterrows():
        ticker = row["ticker"]
        if idx > 0 and idx % 50 == 0:
            logger.info("Fundamentals progress: %d/%d", idx, total)

        data = _fetch_fundamentals(ticker)
        if data is not None:
            fundamentals.append(data)
        else:
            # Fill with NaN so ticker isn't dropped
            fundamentals.append({"ticker": ticker})

        time.sleep(batch_delay)

    fund_df = pd.DataFrame(fundamentals)

    # Cache
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    fund_df.to_parquet(cache_path, index=False)

    return universe.merge(fund_df, on="ticker", how="left")


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
                "revenue_ttm": revenue_ttm,
                "revenue_growth_yoy": revenue_growth,
                "operating_margin": operating_margin,
                "debt_to_equity": debt_to_equity,
                "free_cash_flow": free_cash_flow,
                "pe_ratio": pe_ratio,
                "operating_cash_flow": operating_cf,
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
