"""Insider buying signal computation using EDGAR Form 4 data.

Adapted from claude-backtester/src/backtester/data/edgar_insider.py for
Form 4 parsing logic and edgar_utils.py for rate limiting/retry.

Scores based on:
- Number of unique insiders buying in last 90 days
- Total dollar value of purchases in last 90 days
- CEO/CFO purchases weighted 2x vs director purchases

Only considers PURCHASES (transaction code "P"). Ignores sales, grants, exercises.
"""

import logging
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from edgar.fetcher import EdgarClient
from edgar.insider_parser import parse_insider_transactions, score_insider_buying

logger = logging.getLogger(__name__)


def compute_insider_scores(
    df: pd.DataFrame,
    cache_dir: str = "./data/cache",
    lookback_days: int = 90,
) -> pd.DataFrame:
    """Compute insider buying scores for all tickers.

    Adds column: insider_score (0 = no buying, higher = more insider buying)

    Args:
        df: DataFrame with 'ticker' column.
        cache_dir: Cache directory.
        lookback_days: Number of days to look back for insider purchases.

    Returns:
        DataFrame with insider_score column added.
    """
    cache_path = Path(cache_dir) / f"insider_{date.today().isoformat()}.json"
    if cache_path.exists():
        logger.info("Loading cached insider data from %s", cache_path)
        cached = pd.read_json(cache_path)
        return df.merge(cached[["ticker", "insider_score"]], on="ticker", how="left")

    client = EdgarClient()
    cutoff_date = date.today() - timedelta(days=lookback_days)

    insider_data = []
    total = len(df)

    for idx, row in df.iterrows():
        ticker = row["ticker"]

        if idx > 0 and idx % 20 == 0:
            logger.info("Insider data progress: %d/%d", idx, total)

        try:
            transactions = client.fetch_form4(ticker)
            if transactions is not None and not transactions.empty:
                score = score_insider_buying(transactions, cutoff_date)
            else:
                score = 0.0
        except Exception as e:
            logger.debug("Failed insider data for %s: %s", ticker, e)
            score = 0.0

        insider_data.append({"ticker": ticker, "insider_score": score})

    insider_df = pd.DataFrame(insider_data)

    # Cache
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    insider_df.to_json(cache_path, orient="records", indent=2)

    return df.merge(insider_df, on="ticker", how="left")
