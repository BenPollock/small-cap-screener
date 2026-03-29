"""Pipeline orchestration: universe → filter → score → rank → output.

Coordinates the full screening pipeline, handling errors gracefully
so that one bad ticker doesn't crash the entire run.
"""

import logging

import pandas as pd

logger = logging.getLogger(__name__)


def run_pipeline(
    top: int = 30,
    output_format: str = "terminal",
    skip_edgar: bool = False,
    min_mcap: int = 200,
    max_mcap: int = 2000,
    cache_dir: str = "./data/cache",
) -> pd.DataFrame:
    """Run the full screening pipeline.

    1. Fetch and filter the investable universe
    2. Enrich with fundamental data and apply quality filters
    3. Compute momentum scores
    4. Compute insider buying scores (unless skip_edgar)
    5. Compute composite scores and rank
    6. Render output

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
        min_mcap=min_mcap, max_mcap=max_mcap, cache_dir=cache_dir
    )
    logger.info("Universe: %d tickers", len(universe))

    if universe.empty:
        logger.error("No tickers in universe after filtering. Exiting.")
        return pd.DataFrame()

    # Step 2: Fundamentals + quality filters
    logger.info("Fetching fundamentals...")
    enriched = enrich_fundamentals(universe, cache_dir=cache_dir)
    filtered = apply_quality_filters(enriched)
    logger.info("After quality filters: %d tickers", len(filtered))

    if filtered.empty:
        logger.error("No tickers survived quality filters. Exiting.")
        return pd.DataFrame()

    # Step 3: Momentum
    logger.info("Computing momentum scores...")
    with_momentum = compute_momentum_scores(filtered, cache_dir=cache_dir)

    # Step 4: Insider (optional)
    if skip_edgar:
        logger.info("Skipping EDGAR insider data (--skip-edgar)")
        with_insider = with_momentum
        with_insider["insider_score"] = 0.0
    else:
        logger.info("Fetching EDGAR insider data...")
        with_insider = compute_insider_scores(with_momentum, cache_dir=cache_dir)

    # Step 5: Composite scoring and ranking
    logger.info("Computing composite scores...")
    ranked = compute_composite_scores(with_insider, top=top, skip_edgar=skip_edgar)

    # Step 6: Output
    render_output(ranked, output_format=output_format)
    save_screen(ranked)

    return ranked
