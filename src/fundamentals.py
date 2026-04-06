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
from datetime import date
from pathlib import Path

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)


def enrich_fundamentals(
    universe: pd.DataFrame,
    cache_dir: str = "./data/cache",
) -> pd.DataFrame:
    """Enrich universe DataFrame with fundamental data.

    Adds columns: revenue_ttm, revenue_growth_yoy, operating_margin,
    debt_to_equity, free_cash_flow, pe_ratio, last_fiscal_date

    Args:
        universe: DataFrame with at least a 'ticker' column.
        cache_dir: Directory for caching.

    Returns:
        Universe DataFrame enriched with fundamental columns.
    """
    cache_path = Path(cache_dir) / f"fundamentals_{date.today().isoformat()}.parquet"
    if cache_path.exists():
        cached = pd.read_parquet(cache_path)
        cached_tickers = set(cached["ticker"])
        input_tickers = set(universe["ticker"])
        missing = input_tickers - cached_tickers
        if not missing:
            logger.info("Loading cached fundamentals from %s", cache_path)
            return universe.merge(cached, on="ticker", how="left")
        logger.warning(
            "Fundamentals cache stale: %d/%d tickers missing. Recomputing.",
            len(missing), len(input_tickers),
        )

    cache_path.parent.mkdir(parents=True, exist_ok=True)

    tickers = universe["ticker"].tolist()
    total = len(tickers)
    fundamentals: list[dict] = []

    for i, ticker in enumerate(tickers, 1):
        if i % 50 == 0:
            logger.info("Fundamentals progress: %d/%d", i, total)
        try:
            data = _fetch_fundamentals(ticker)
            if data is not None:
                fundamentals.append(data)
            else:
                fundamentals.append({"ticker": ticker})
        except Exception:
            fundamentals.append({"ticker": ticker})

    fund_df = pd.DataFrame(fundamentals)

    # Cache
    fund_df.to_parquet(cache_path, index=False)

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


def _fetch_fundamentals(
    ticker: str, max_retries: int = 2,
) -> dict | None:
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


def _run_quality_filters(
    df: pd.DataFrame,
    growth_threshold: float,
    de_threshold: float,
    recency_months: int,
) -> pd.DataFrame:
    """Apply quality filters with the given thresholds.

    Returns filtered DataFrame with per-stage logging.
    """
    # Revenue growth (keep NaN — don't penalize missing data)
    prev = len(df)
    mask_growth = df["revenue_growth_yoy"].isna() | (
        df["revenue_growth_yoy"] > growth_threshold
    )
    df = df[mask_growth].copy()
    logger.info(
        "After revenue growth filter (>%+.0f%%): %d/%d",
        growth_threshold * 100, len(df), prev,
    )

    # Positive operating cash flow (keep NaN — don't penalize missing data)
    prev = len(df)
    mask_cf = df["operating_cash_flow"].isna() | (df["operating_cash_flow"] > 0)
    df = df[mask_cf].copy()
    logger.info("After operating cash flow filter: %d/%d", len(df), prev)

    # Debt to equity
    prev = len(df)
    mask_de = df["debt_to_equity"].isna() | (df["debt_to_equity"] < de_threshold)
    df = df[mask_de].copy()
    logger.info(
        "After debt-to-equity filter (<%.1f): %d/%d", de_threshold, len(df), prev,
    )

    # Recent financials
    prev = len(df)
    if "last_fiscal_date" in df.columns:
        cutoff = pd.Timestamp(date.today()) - pd.DateOffset(months=recency_months)
        mask_recent = df["last_fiscal_date"].isna() | (
            pd.to_datetime(df["last_fiscal_date"]) >= cutoff
        )
        df = df[mask_recent].copy()
        logger.info(
            "After recent financials filter (%d mo): %d/%d",
            recency_months, len(df), prev,
        )

    return df


# Relaxation tiers: (growth_threshold, de_threshold, recency_months)
_FILTER_TIERS = [
    (-0.05, 2.0, 12),   # default: >-5% growth, D/E<2, 12-month recency
    (-0.10, 2.5, 12),   # tier 1 relaxation
    (-0.20, 3.0, 18),   # tier 2 relaxation
]

_MIN_SURVIVORS = 100


def apply_quality_filters(df: pd.DataFrame) -> pd.DataFrame:
    """Apply quality filters to the enriched universe.

    Filters:
    - Revenue growth > -5% YoY (default)
    - Positive operating cash flow in latest quarter
    - Debt-to-equity < 2.0
    - Has reported financials in the last 12 months

    Logs survivors at each stage. Progressively relaxes thresholds
    if fewer than 100 tickers survive all filters combined.
    """
    if df.empty:
        return df

    initial = len(df)

    for tier_idx, (growth_th, de_th, recency_mo) in enumerate(_FILTER_TIERS):
        result = _run_quality_filters(df, growth_th, de_th, recency_mo)

        if len(result) >= _MIN_SURVIVORS or tier_idx == len(_FILTER_TIERS) - 1:
            if tier_idx > 0:
                logger.warning(
                    "Relaxed to tier %d (growth>%+.0f%%, D/E<%.1f, %dmo) — %d survivors",
                    tier_idx, growth_th * 100, de_th, recency_mo, len(result),
                )
            break

        logger.warning(
            "Only %d survivors at tier %d, relaxing filters...",
            len(result), tier_idx,
        )

    result.reset_index(drop=True, inplace=True)
    logger.info("Quality filter summary: %d/%d survived", len(result), initial)
    return result
