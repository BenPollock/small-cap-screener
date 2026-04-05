"""Price momentum calculations: 6-month ROC, 1-month ROC, relative strength.

Uses yfinance for price data with caching to parquet.
Implements Jegadeesh-Titman momentum with short-term reversal filter.

Adapted from claude-backtester/src/backtester/data/sources/yahoo.py for
yfinance data fetching patterns.
"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf

logger = logging.getLogger(__name__)

# Sector ETF mapping for relative strength calculation
SECTOR_ETFS = {
    "Technology": "XLK",
    "Healthcare": "XLV",
    "Financial Services": "XLF",
    "Financials": "XLF",
    "Consumer Cyclical": "XLY",
    "Consumer Defensive": "XLP",
    "Energy": "XLE",
    "Industrials": "XLI",
    "Basic Materials": "XLB",
    "Materials": "XLB",
    "Communication Services": "XLC",
    "Utilities": "XLU",
    "Real Estate": "XLRE",
}


def compute_momentum_scores(
    df: pd.DataFrame,
    cache_dir: str = "./data/cache",
    max_workers: int = 8,
) -> pd.DataFrame:
    """Compute momentum scores for all tickers in the DataFrame.

    Adds columns: roc_6m, roc_1m, sector_roc_6m, relative_strength, momentum_score

    Composite momentum score:
    - Primary: 6-month ROC (higher = better)
    - Penalty: if 1-month ROC is in top 10%, reduce score by 50% (mean reversion risk)

    Args:
        df: DataFrame with 'ticker' and 'sector' columns.
        cache_dir: Directory for caching price data.

    Returns:
        DataFrame with momentum columns added.
    """
    cache_path = Path(cache_dir) / f"momentum_{date.today().isoformat()}.parquet"
    if cache_path.exists():
        logger.info("Loading cached momentum from %s", cache_path)
        cached = pd.read_parquet(cache_path)
        return df.merge(cached, on="ticker", how="left")

    # Pre-fetch sector ETF data
    sector_momentum = _fetch_sector_etf_momentum()

    # Fetch price history for all tickers concurrently
    ticker_sectors = list(zip(df["ticker"].tolist(), df.get("sector", pd.Series([""] * len(df))).tolist()))
    total = len(ticker_sectors)
    momentum_data = []
    completed = 0

    from src.universe import _make_pooled_session
    session = _make_pooled_session(max_workers)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_compute_single_momentum, t, s, sector_momentum, session=session): t
            for t, s in ticker_sectors
        }
        for future in as_completed(futures):
            completed += 1
            if completed % 50 == 0:
                logger.info("Momentum progress: %d/%d", completed, total)
            try:
                momentum_data.append(future.result())
            except Exception as e:
                ticker = futures[future]
                logger.debug("Failed momentum for %s: %s", ticker, e)
                momentum_data.append({"ticker": ticker, "roc_6m": None, "roc_1m": None,
                                      "sector_roc_6m": None, "relative_strength": None,
                                      "momentum_score": None})

    session.close()
    mom_df = pd.DataFrame(momentum_data)

    # Apply short-term reversal penalty
    mom_df = _apply_reversal_penalty(mom_df)

    # Cache
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    mom_df.to_parquet(cache_path, index=False)

    return df.merge(mom_df, on="ticker", how="left")


def _fetch_sector_etf_momentum() -> dict[str, float]:
    """Fetch 6-month ROC for sector ETFs concurrently.

    Returns dict mapping sector name to 6-month ROC.
    """
    sector_rocs = {}
    unique_etfs = set(SECTOR_ETFS.values())

    def _fetch_one_etf(etf: str) -> tuple[str, float | None]:
        try:
            t = yf.Ticker(etf)
            hist = t.history(period="1y")
            if len(hist) >= 126:
                roc_6m = (hist["Close"].iloc[-1] / hist["Close"].iloc[-126] - 1) * 100
                return etf, roc_6m
        except Exception as e:
            logger.debug("Failed to fetch sector ETF %s: %s", etf, e)
        return etf, None

    with ThreadPoolExecutor(max_workers=min(len(unique_etfs), 8)) as executor:
        results = executor.map(_fetch_one_etf, unique_etfs)

    etf_rocs = {etf: roc for etf, roc in results if roc is not None}
    for sector, sector_etf in SECTOR_ETFS.items():
        if sector_etf in etf_rocs:
            sector_rocs[sector] = etf_rocs[sector_etf]

    return sector_rocs


def _compute_single_momentum(
    ticker: str,
    sector: str,
    sector_momentum: dict[str, float],
    max_retries: int = 2,
    session: requests.Session | None = None,
) -> dict:
    """Compute momentum metrics for a single ticker."""
    result = {
        "ticker": ticker,
        "roc_6m": None,
        "roc_1m": None,
        "sector_roc_6m": sector_momentum.get(sector),
        "relative_strength": None,
        "momentum_score": None,
    }

    for attempt in range(max_retries):
        try:
            t = yf.Ticker(ticker, session=session)
            hist = t.history(period="1y")

            if hist.empty or len(hist) < 21:
                return result

            close = hist["Close"]

            # 6-month ROC (126 trading days)
            if len(close) >= 126:
                result["roc_6m"] = (close.iloc[-1] / close.iloc[-126] - 1) * 100
            elif len(close) >= 63:
                # Fallback to available history
                result["roc_6m"] = (close.iloc[-1] / close.iloc[0] - 1) * 100

            # 1-month ROC (21 trading days)
            if len(close) >= 21:
                result["roc_1m"] = (close.iloc[-1] / close.iloc[-21] - 1) * 100

            # Relative strength vs sector
            sector_roc = sector_momentum.get(sector)
            if result["roc_6m"] is not None and sector_roc is not None:
                result["relative_strength"] = result["roc_6m"] - sector_roc

            # Raw momentum score = 6m ROC (penalty applied later across universe)
            result["momentum_score"] = result["roc_6m"]

            return result

        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(1.0 * (attempt + 1))
            else:
                logger.debug("Failed momentum for %s: %s", ticker, e)

    return result


def _apply_reversal_penalty(df: pd.DataFrame) -> pd.DataFrame:
    """Apply short-term reversal penalty to momentum scores.

    If 1-month ROC is in top 10% of the universe (too extended),
    reduce momentum_score by 50%.
    """
    if df.empty or df["roc_1m"].isna().all():
        return df

    threshold = df["roc_1m"].quantile(0.90)
    extended_mask = df["roc_1m"].notna() & (df["roc_1m"] >= threshold)

    df = df.copy()
    df.loc[extended_mask, "momentum_score"] = df.loc[extended_mask, "momentum_score"] * 0.5

    n_penalized = extended_mask.sum()
    if n_penalized > 0:
        logger.info(
            "Applied reversal penalty to %d tickers (1m ROC >= %.1f%%)",
            n_penalized,
            threshold,
        )

    return df
