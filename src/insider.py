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
from concurrent.futures import ThreadPoolExecutor, as_completed
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
    max_workers: int = 10,
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

    partial_path = Path(cache_dir) / f"_insider_partial_{date.today().isoformat()}.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)

    # Resume from partial checkpoint if available
    done_tickers: set[str] = set()
    insider_data: list[dict] = []
    if partial_path.exists():
        partial_df = pd.read_json(partial_path)
        insider_data = partial_df.to_dict("records")
        done_tickers = set(partial_df["ticker"])
        logger.info("Resuming insider data: %d tickers already completed", len(done_tickers))

    remaining = [t for t in df["ticker"].tolist() if t not in done_tickers]
    total = len(remaining) + len(done_tickers)
    completed = len(done_tickers)
    new_since_checkpoint = 0
    _checkpoint_lock = __import__("threading").Lock()

    def _fetch_one(ticker: str) -> dict:
        try:
            transactions = client.fetch_form4(ticker)
            if transactions is not None and not transactions.empty:
                score = score_insider_buying(transactions, cutoff_date)
            else:
                score = 0.0
        except Exception as e:
            logger.debug("Failed insider data for %s: %s", ticker, e)
            score = 0.0
        return {"ticker": ticker, "insider_score": score}

    # 10 workers matches SEC's 10 req/s limit; the shared RateLimiter
    # in EdgarClient serializes actual HTTP calls to stay compliant.
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_fetch_one, t): t for t in remaining}
        for future in as_completed(futures):
            result = future.result()
            completed += 1
            if completed % 20 == 0:
                logger.info("Insider data progress: %d/%d", completed, total)
            insider_data.append(result)
            new_since_checkpoint += 1

            # Periodic checkpoint
            if new_since_checkpoint >= 20:
                with _checkpoint_lock:
                    pd.DataFrame(insider_data).to_json(partial_path, orient="records", indent=2)
                    new_since_checkpoint = 0

    insider_df = pd.DataFrame(insider_data)

    # Final cache + cleanup
    insider_df.to_json(cache_path, orient="records", indent=2)
    if partial_path.exists():
        partial_path.unlink()

    return df.merge(insider_df, on="ticker", how="left")
