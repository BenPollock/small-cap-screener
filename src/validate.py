"""ETF factor validation: check that factor premia exist historically.

Compares factor ETFs against benchmarks to validate that small-cap
momentum, value, quality, and size premia are real.

This doesn't prove our screener works, but proves the underlying
factors have real premia in ETF returns.
"""

import logging
from datetime import date

import numpy as np
import pandas as pd
import yfinance as yf
from rich.console import Console
from rich.table import Table

logger = logging.getLogger(__name__)

# Factor ETF comparisons: (factor_name, factor_etf, benchmark_etf)
FACTOR_COMPARISONS = [
    ("Small-Cap Value", "SLYV", "SLY", "S&P 600 Value vs S&P 600"),
    ("Small-Cap Momentum", "XSMO", "SLY", "S&P 600 Momentum vs S&P 600"),
    ("Quality", "SPHQ", "SPY", "S&P 500 Quality vs S&P 500"),
    ("Size Premium", "IWM", "SPY", "Russell 2000 vs S&P 500"),
    ("Insider Buying", "KNOW", "SPY", "Insider ETF vs S&P 500"),
]


def run_validation(period: str = "10y") -> pd.DataFrame:
    """Run factor validation and display results.

    Args:
        period: yfinance period string (e.g., '10y', '5y').

    Returns:
        DataFrame with validation results.
    """
    results = []

    for factor_name, factor_etf, benchmark_etf, description in FACTOR_COMPARISONS:
        logger.info("Validating %s: %s vs %s", factor_name, factor_etf, benchmark_etf)
        result = _compare_etfs(factor_name, factor_etf, benchmark_etf, description, period)
        if result:
            results.append(result)

    if not results:
        print("No validation data available.")
        return pd.DataFrame()

    results_df = pd.DataFrame(results)
    _render_validation_table(results_df)
    return results_df


def _compare_etfs(
    factor_name: str,
    factor_etf: str,
    benchmark_etf: str,
    description: str,
    period: str,
) -> dict | None:
    """Compare a factor ETF against a benchmark.

    Returns dict with: factor, etf, benchmark, period, cagr_factor,
    cagr_benchmark, sharpe_factor, sharpe_benchmark, max_dd_factor,
    excess_return
    """
    try:
        factor_hist = yf.Ticker(factor_etf).history(period=period)
        bench_hist = yf.Ticker(benchmark_etf).history(period=period)

        if factor_hist.empty or bench_hist.empty:
            logger.warning("No data for %s or %s", factor_etf, benchmark_etf)
            return None

        # Align dates
        common_dates = factor_hist.index.intersection(bench_hist.index)
        if len(common_dates) < 252:  # Need at least 1 year
            logger.warning("Insufficient overlapping data for %s vs %s", factor_etf, benchmark_etf)
            return None

        factor_prices = factor_hist.loc[common_dates, "Close"]
        bench_prices = bench_hist.loc[common_dates, "Close"]

        years = len(common_dates) / 252

        # CAGR
        factor_cagr = _compute_cagr(factor_prices, years)
        bench_cagr = _compute_cagr(bench_prices, years)

        # Sharpe ratio (annualized, assuming 0% risk-free for simplicity)
        factor_sharpe = _compute_sharpe(factor_prices)
        bench_sharpe = _compute_sharpe(bench_prices)

        # Max drawdown
        factor_maxdd = _compute_max_drawdown(factor_prices)

        return {
            "Factor": factor_name,
            "ETF": factor_etf,
            "Benchmark": benchmark_etf,
            "Description": description,
            "Period": f"{years:.1f}y",
            "CAGR": factor_cagr,
            "Bench CAGR": bench_cagr,
            "Sharpe": factor_sharpe,
            "Bench Sharpe": bench_sharpe,
            "MaxDD": factor_maxdd,
            "Excess Return": factor_cagr - bench_cagr,
        }

    except Exception as e:
        logger.warning("Failed to compare %s vs %s: %s", factor_etf, benchmark_etf, e)
        return None


def _compute_cagr(prices: pd.Series, years: float) -> float:
    """Compute CAGR from a price series."""
    if len(prices) < 2 or years <= 0:
        return 0.0
    total_return = prices.iloc[-1] / prices.iloc[0]
    return (total_return ** (1 / years) - 1) * 100


def _compute_sharpe(prices: pd.Series, risk_free: float = 0.0) -> float:
    """Compute annualized Sharpe ratio from daily prices."""
    daily_returns = prices.pct_change().dropna()
    if len(daily_returns) < 20:
        return 0.0
    excess = daily_returns - risk_free / 252
    if excess.std() == 0:
        return 0.0
    return float(np.sqrt(252) * excess.mean() / excess.std())


def _compute_max_drawdown(prices: pd.Series) -> float:
    """Compute maximum drawdown percentage."""
    cummax = prices.cummax()
    drawdown = (prices - cummax) / cummax
    return float(drawdown.min() * 100)


def _render_validation_table(df: pd.DataFrame) -> None:
    """Render validation results as a Rich table."""
    console = Console()
    table = Table(title="Factor Validation — ETF Premia Check")

    table.add_column("Factor")
    table.add_column("ETF")
    table.add_column("Benchmark")
    table.add_column("Period")
    table.add_column("CAGR", justify="right")
    table.add_column("Bench CAGR", justify="right")
    table.add_column("Sharpe", justify="right")
    table.add_column("MaxDD", justify="right")
    table.add_column("Excess", justify="right")

    for _, row in df.iterrows():
        excess = row["Excess Return"]
        excess_str = f"{excess:+.1f}%"

        table.add_row(
            row["Factor"],
            row["ETF"],
            row["Benchmark"],
            row["Period"],
            f"{row['CAGR']:.1f}%",
            f"{row['Bench CAGR']:.1f}%",
            f"{row['Sharpe']:.2f}",
            f"{row['MaxDD']:.1f}%",
            excess_str,
        )

    console.print(table)
