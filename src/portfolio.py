"""Paper portfolio tracker: log picks, track forward performance.

Stores state in data/portfolio/portfolio.json. Each cohort records:
date, tickers, entry prices, composite scores. The 'show' command
fetches current prices and computes returns vs SPY.
"""

import json
import logging
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

PORTFOLIO_PATH = Path("./data/portfolio/portfolio.json")


def log_portfolio(top: int = 10, cache_dir: str = "./data/cache") -> None:
    """Log today's top picks to the paper portfolio.

    Runs the pipeline to get top picks, records entry prices.
    """
    from src.pipeline import run_pipeline

    # Run pipeline to get top picks
    results = run_pipeline(
        top=top,
        output_format="terminal",
        skip_edgar=False,
        cache_dir=cache_dir,
    )

    if results.empty:
        logger.error("No results from pipeline. Nothing to log.")
        return

    # Fetch current prices for entry
    tickers = results["ticker"].tolist()
    entry_prices = _fetch_current_prices(tickers)

    cohort = {
        "date": date.today().isoformat(),
        "timestamp": datetime.now().isoformat(),
        "tickers": tickers,
        "entry_prices": entry_prices,
        "scores": results.set_index("ticker")["composite_score"].to_dict(),
    }

    # Load existing portfolio
    portfolio = _load_portfolio()
    portfolio["cohorts"].append(cohort)
    _save_portfolio(portfolio)

    logger.info("Logged %d picks to portfolio for %s", len(tickers), date.today())
    print(f"Logged {len(tickers)} picks to paper portfolio.")


def show_portfolio() -> None:
    """Show paper portfolio performance for all logged cohorts."""
    portfolio = _load_portfolio()

    if not portfolio["cohorts"]:
        print("No cohorts logged yet. Run 'screener portfolio log' first.")
        return

    from rich.console import Console
    from rich.table import Table

    console = Console()
    table = Table(title="Paper Portfolio Performance")
    table.add_column("Cohort Date")
    table.add_column("Holding Period")
    table.add_column("# Tickers")
    table.add_column("Portfolio Return", justify="right")
    table.add_column("SPY Return", justify="right")
    table.add_column("Excess Return", justify="right")

    for cohort in portfolio["cohorts"]:
        cohort_date = date.fromisoformat(cohort["date"])
        holding_days = (date.today() - cohort_date).days

        tickers = cohort["tickers"]
        entry_prices = cohort["entry_prices"]

        # Fetch current prices
        current_prices = _fetch_current_prices(tickers)

        # Compute equal-weight portfolio return
        returns = []
        for t in tickers:
            entry = entry_prices.get(t)
            current = current_prices.get(t)
            if entry and current and entry > 0:
                returns.append((current - entry) / entry)

        portfolio_return = sum(returns) / len(returns) * 100 if returns else 0.0

        # SPY return over same period
        spy_return = _compute_spy_return(cohort_date)

        excess = portfolio_return - spy_return

        table.add_row(
            cohort["date"],
            f"{holding_days}d",
            str(len(tickers)),
            f"{portfolio_return:+.1f}%",
            f"{spy_return:+.1f}%",
            f"{excess:+.1f}%",
        )

    console.print(table)


def _fetch_current_prices(tickers: list[str]) -> dict[str, float]:
    """Fetch current prices for a list of tickers."""
    prices = {}
    for ticker in tickers:
        try:
            t = yf.Ticker(ticker)
            info = t.info
            price = info.get("currentPrice") or info.get("regularMarketPrice")
            if price:
                prices[ticker] = float(price)
        except Exception as e:
            logger.debug("Failed to fetch price for %s: %s", ticker, e)
    return prices


def _compute_spy_return(start_date: date) -> float:
    """Compute SPY return from start_date to today."""
    try:
        spy = yf.Ticker("SPY")
        hist = spy.history(start=start_date.isoformat(), end=date.today().isoformat())
        if len(hist) >= 2:
            return (hist["Close"].iloc[-1] / hist["Close"].iloc[0] - 1) * 100
    except Exception as e:
        logger.debug("Failed to fetch SPY return: %s", e)
    return 0.0


def _load_portfolio() -> dict:
    """Load portfolio state from disk."""
    if PORTFOLIO_PATH.exists():
        with open(PORTFOLIO_PATH) as f:
            return json.load(f)
    return {"cohorts": []}


def _save_portfolio(portfolio: dict) -> None:
    """Save portfolio state to disk."""
    PORTFOLIO_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(PORTFOLIO_PATH, "w") as f:
        json.dump(portfolio, f, indent=2, default=str)
