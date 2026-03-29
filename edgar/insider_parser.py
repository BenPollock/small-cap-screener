"""Form 4 insider transaction parsing and scoring.

Adapted from claude-backtester/src/backtester/data/edgar_insider.py.

Parses Form 4 transactions and scores insider buying activity:
- Filters to PURCHASES only (transaction code "P")
- Scores based on unique insiders buying, dollar value, title weighting
- CEO/CFO purchases weighted 2x vs director purchases
"""

import logging
from datetime import date

import pandas as pd

logger = logging.getLogger(__name__)

# Title keywords that get 2x weighting
EXECUTIVE_TITLES = {"ceo", "chief executive", "cfo", "chief financial", "president"}
DIRECTOR_TITLES = {"director", "board"}


def parse_insider_transactions(df: pd.DataFrame) -> pd.DataFrame:
    """Filter to purchases only and add computed columns.

    Args:
        df: Raw Form 4 transaction DataFrame from EdgarClient.

    Returns:
        DataFrame filtered to purchases with dollar_value column added.
    """
    if df.empty:
        return df

    # Filter to purchases only
    purchases = df[df["transaction_type"] == "P"].copy()

    if purchases.empty:
        return purchases

    # Compute dollar value of each purchase
    purchases["dollar_value"] = purchases["shares"].abs() * purchases["price"]

    return purchases


def score_insider_buying(
    transactions: pd.DataFrame,
    cutoff_date: date,
) -> float:
    """Score insider buying activity for a ticker.

    Score components:
    1. Number of unique insiders buying (log scale)
    2. Total dollar value of purchases (log scale)
    3. Executive purchases weighted 2x

    Args:
        transactions: Raw Form 4 transactions from EdgarClient.
        cutoff_date: Only consider transactions after this date.

    Returns:
        Insider buying score (0 = no buying, higher = more activity).
    """
    if transactions.empty:
        return 0.0

    # Filter to purchases only
    purchases = parse_insider_transactions(transactions)
    if purchases.empty:
        return 0.0

    # Filter to recent transactions
    if "transaction_date" in purchases.columns:
        purchases = purchases[
            purchases["transaction_date"].apply(
                lambda d: d >= cutoff_date if isinstance(d, date) else False
            )
        ].copy()

    if purchases.empty:
        return 0.0

    # Component 1: Number of unique insiders
    unique_insiders = purchases["insider_name"].nunique()

    # Component 2: Total dollar value
    total_dollar_value = purchases["dollar_value"].sum()

    # Component 3: Executive weighting
    exec_weight = 0.0
    for _, row in purchases.iterrows():
        title = str(row.get("insider_title", "")).lower()
        if any(kw in title for kw in EXECUTIVE_TITLES):
            exec_weight += 2.0
        elif any(kw in title for kw in DIRECTOR_TITLES):
            exec_weight += 1.0
        else:
            exec_weight += 1.0

    # Combine components (log scale to prevent outlier dominance)
    import math

    score = 0.0

    # Unique insider count (0-40 points)
    if unique_insiders > 0:
        score += min(40, math.log1p(unique_insiders) * 20)

    # Dollar value (0-40 points, log scaled)
    if total_dollar_value > 0:
        score += min(40, math.log10(max(1, total_dollar_value)) * 5)

    # Executive weighting bonus (0-20 points)
    if exec_weight > 0:
        score += min(20, exec_weight * 3)

    return round(score, 2)
