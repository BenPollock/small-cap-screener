"""Click-based CLI entry point for the small-cap screener."""

import logging

import click


@click.group()
@click.option("-v", "--verbose", count=True, help="Increase verbosity (-v for info, -vv for debug).")
def cli(verbose):
    """Small-cap stock screener — systematic screening pipeline."""
    level = logging.WARNING
    if verbose == 1:
        level = logging.INFO
    elif verbose >= 2:
        level = logging.DEBUG
    logging.basicConfig(
        level=level,
        format="%(levelname)s | %(name)s | %(message)s",
    )


@cli.command()
@click.option("--top", default=30, help="Number of stocks to display.")
@click.option(
    "--output",
    "output_format",
    type=click.Choice(["terminal", "csv", "markdown"]),
    default="terminal",
    help="Output format.",
)
@click.option("--skip-edgar", is_flag=True, help="Skip EDGAR insider data fetching.")
@click.option("--min-mcap", default=200, type=int, help="Minimum market cap in millions.")
@click.option("--max-mcap", default=2000, type=int, help="Maximum market cap in millions.")
@click.option("--cache-dir", default="./data/cache", help="Cache directory.")
@click.option("--workers", default=8, type=int, help="Max concurrent fetch threads per stage (default: 8).")
def run(top, output_format, skip_edgar, min_mcap, max_mcap, cache_dir, workers):
    """Run the full screening pipeline and output top picks."""
    from src.pipeline import run_pipeline

    run_pipeline(
        top=top,
        output_format=output_format,
        skip_edgar=skip_edgar,
        min_mcap=min_mcap,
        max_mcap=max_mcap,
        cache_dir=cache_dir,
        max_workers=workers,
    )


@cli.group()
def portfolio():
    """Paper portfolio tracking commands."""
    pass


@portfolio.command("log")
@click.option("--top", default=10, help="Number of top picks to log.")
@click.option("--cache-dir", default="./data/cache", help="Cache directory.")
def portfolio_log(top, cache_dir):
    """Log today's top picks to the paper portfolio."""
    from src.portfolio import log_portfolio

    log_portfolio(top=top, cache_dir=cache_dir)


@portfolio.command("show")
def portfolio_show():
    """Show paper portfolio performance."""
    from src.portfolio import show_portfolio

    show_portfolio()


@cli.command()
@click.option("--period", default="10y", help="yfinance period string for historical data.")
def validate(period):
    """Run ETF factor validation to check that factor premia exist."""
    from src.validate import run_validation

    run_validation(period=period)


if __name__ == "__main__":
    cli()
