"""Pipeline orchestration: universe → filter → score → rank → output.

Coordinates the full screening pipeline, handling errors gracefully
so that one bad ticker doesn't crash the entire run.

Fundamentals and momentum are independent after universe filtering and
run concurrently (Proposal 3 from performance analysis).
"""

import logging
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

logger = logging.getLogger(__name__)


def run_pipeline(
    top: int = 30,
    output_format: str = "terminal",
    skip_edgar: bool = False,
    min_mcap: int = 200,
    max_mcap: int = 2000,
    cache_dir: str = "./data/cache",
    max_workers: int = 8,
) -> pd.DataFrame:
    """Run the full screening pipeline.

    1. Fetch and filter the investable universe
    2. Enrich with fundamental data and apply quality filters
    3. Compute momentum scores
    4. Compute insider buying scores (unless skip_edgar)
    5. Compute composite scores and rank
    6. Render output

    Args:
        max_workers: Max concurrent fetch threads per stage (default 8).

    Returns the final ranked DataFrame.
    """
    from src.universe import get_universe
    from src.fundamentals import enrich_fundamentals, apply_quality_filters
    from src.momentum import compute_momentum_scores
    from src.insider import compute_insider_scores
    from src.scorer import compute_composite_scores
    from src.output import render_output, save_screen

    # Step 1: Universe
    logger.info("Fetching investable universe...")
    universe = get_universe(
        min_mcap=min_mcap, max_mcap=max_mcap, cache_dir=cache_dir,
        max_workers=max_workers,
    )
    logger.info("Universe: %d tickers", len(universe))

    if universe.empty:
        logger.error("No tickers in universe after filtering. Exiting.")
        return pd.DataFrame()

    # Steps 2+3: Fundamentals and momentum run concurrently — they hit
    # different yfinance endpoints and are independent after universe filtering.
    logger.info("Fetching fundamentals and momentum concurrently...")

    with ThreadPoolExecutor(max_workers=2) as stage_executor:
        fund_future = stage_executor.submit(
            enrich_fundamentals, universe, cache_dir, max_workers,
        )
        mom_future = stage_executor.submit(
            compute_momentum_scores, universe, cache_dir, max_workers,
        )

        enriched = fund_future.result()
        momentum_df = mom_future.result()

    filtered = apply_quality_filters(enriched)
    logger.info("After quality filters: %d tickers", len(filtered))

    if filtered.empty:
        logger.error("No tickers survived quality filters. Exiting.")
        return pd.DataFrame()

    # Merge momentum into quality-filtered tickers
    mom_cols = ["ticker", "roc_6m", "roc_1m", "sector_roc_6m", "relative_strength", "momentum_score"]
    available_mom_cols = [c for c in mom_cols if c in momentum_df.columns]
    with_momentum = filtered.merge(momentum_df[available_mom_cols], on="ticker", how="left")

    # Step 4: Insider (optional)
    if skip_edgar:
        logger.info("Skipping EDGAR insider data (--skip-edgar)")
        with_insider = with_momentum
        with_insider["insider_score"] = 0.0
    else:
        logger.info("Fetching EDGAR insider data...")
        with_insider = compute_insider_scores(
            with_momentum, cache_dir=cache_dir, max_workers=min(max_workers, 10),
        )

    # Step 5: Composite scoring and ranking
    logger.info("Computing composite scores...")
    ranked = compute_composite_scores(with_insider, top=top, skip_edgar=skip_edgar)

    # Step 6: Output
    render_output(ranked, output_format=output_format)
    save_screen(ranked)

    return ranked
