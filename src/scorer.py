"""Composite scoring: normalized factor scores with configurable weights.

Normalizes each signal to 0-100 percentile rank within the universe,
then computes a weighted composite score.

Default weights:
- 40% momentum, 30% quality, 30% insider
- Without EDGAR: 55% momentum, 45% quality
"""

import logging

import pandas as pd

logger = logging.getLogger(__name__)


def compute_composite_scores(
    df: pd.DataFrame,
    top: int = 30,
    skip_edgar: bool = False,
    momentum_weight: float | None = None,
    quality_weight: float | None = None,
    insider_weight: float | None = None,
) -> pd.DataFrame:
    """Compute composite scores and return top N ranked tickers.

    Args:
        df: DataFrame with momentum, fundamental, and insider columns.
        top: Number of top tickers to return.
        skip_edgar: If True, redistribute insider weight to momentum/quality.
        momentum_weight: Override momentum weight (default: 0.40 or 0.55).
        quality_weight: Override quality weight (default: 0.30 or 0.45).
        insider_weight: Override insider weight (default: 0.30 or 0.0).

    Returns:
        Top N tickers ranked by composite score descending.
    """
    if df.empty:
        return df

    df = df.copy()

    # Set weights
    if skip_edgar:
        w_mom = momentum_weight or 0.55
        w_qual = quality_weight or 0.45
        w_ins = 0.0
    else:
        w_mom = momentum_weight or 0.40
        w_qual = quality_weight or 0.30
        w_ins = insider_weight or 0.30

    # Normalize momentum score to 0-100 percentile rank
    df["momentum_rank"] = _percentile_rank(df, "momentum_score")

    # Compute quality composite
    df["quality_score"] = _compute_quality_score(df)
    df["quality_rank"] = _percentile_rank(df, "quality_score")

    # Normalize insider score
    df["insider_rank"] = _percentile_rank(df, "insider_score")

    # Composite score
    df["composite_score"] = (
        w_mom * df["momentum_rank"]
        + w_qual * df["quality_rank"]
        + w_ins * df["insider_rank"]
    )

    # Rank and sort
    df = df.sort_values("composite_score", ascending=False).reset_index(drop=True)
    df["rank"] = range(1, len(df) + 1)

    # Select top N
    result = df.head(top).copy()

    logger.info(
        "Scoring complete. Weights: momentum=%.0f%%, quality=%.0f%%, insider=%.0f%%",
        w_mom * 100,
        w_qual * 100,
        w_ins * 100,
    )
    logger.info("Top ticker: %s (score: %.1f)", result.iloc[0]["ticker"], result.iloc[0]["composite_score"])

    return result


def _percentile_rank(df: pd.DataFrame, column: str) -> pd.Series:
    """Compute 0-100 percentile rank for a column.

    NaN values get rank 0. Handles edge cases:
    - All same values → all get 50
    - Single ticker → gets 50
    - All NaN → all get 0
    """
    series = df[column].copy()

    if series.isna().all():
        return pd.Series(0.0, index=df.index)

    if series.notna().sum() == 1:
        result = pd.Series(0.0, index=df.index)
        result[series.notna()] = 50.0
        return result

    # Check if all non-NaN values are the same
    non_null = series.dropna()
    if non_null.nunique() == 1:
        result = pd.Series(0.0, index=df.index)
        result[series.notna()] = 50.0
        return result

    # Standard percentile rank
    result = series.rank(pct=True, na_option="bottom") * 100
    result[series.isna()] = 0.0
    return result


def _compute_quality_score(df: pd.DataFrame) -> pd.Series:
    """Compute quality composite from fundamental factors.

    Quality = average of percentile ranks for:
    - Revenue growth (higher = better)
    - Operating margin (higher = better)
    - Free cash flow (positive = better)

    Missing values get neutral (50th percentile) treatment.
    """
    components = []

    if "revenue_growth_yoy" in df.columns:
        components.append(_percentile_rank(df, "revenue_growth_yoy"))

    if "operating_margin" in df.columns:
        components.append(_percentile_rank(df, "operating_margin"))

    if "free_cash_flow" in df.columns:
        components.append(_percentile_rank(df, "free_cash_flow"))

    if not components:
        return pd.Series(50.0, index=df.index)

    quality = pd.concat(components, axis=1).mean(axis=1)
    return quality
