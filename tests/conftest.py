"""Shared test fixtures for the small-cap screener test suite.

Provides mock yfinance responses, mock EDGAR data, and sample DataFrames
for use across unit and E2E tests.
"""

import json
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Sample DataFrames
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_universe():
    """Sample universe DataFrame with 5 tickers."""
    return pd.DataFrame({
        "ticker": ["ACME", "BETA", "GAMA", "DELT", "EPSI"],
        "company_name": [
            "Acme Corp", "Beta Inc", "Gamma Holdings",
            "Delta Systems", "Epsilon Tech",
        ],
        "market_cap": [
            500_000_000, 800_000_000, 1_200_000_000,
            300_000_000, 1_800_000_000,
        ],
        "avg_volume": [200_000, 500_000, 300_000, 150_000, 1_000_000],
        "avg_dollar_volume": [
            2_000_000, 10_000_000, 6_000_000, 1_500_000, 50_000_000,
        ],
        "sector": [
            "Technology", "Healthcare", "Industrials",
            "Technology", "Financial Services",
        ],
        "industry": [
            "Software", "Biotechnology", "Aerospace",
            "Semiconductors", "Banks",
        ],
        "exchange": ["NMS", "NMS", "NYQ", "NMS", "NYQ"],
    })


@pytest.fixture
def sample_fundamentals():
    """Sample fundamentals data matching sample_universe tickers."""
    return pd.DataFrame({
        "ticker": ["ACME", "BETA", "GAMA", "DELT", "EPSI"],
        "revenue_ttm": [100e6, 50e6, 200e6, 30e6, 500e6],
        "revenue_growth_yoy": [0.15, 0.25, 0.05, -0.10, 0.08],
        "operating_margin": [0.12, 0.20, 0.08, -0.05, 0.15],
        "debt_to_equity": [0.5, 0.3, 1.5, 0.8, 1.2],
        "free_cash_flow": [10e6, 5e6, 15e6, -2e6, 50e6],
        "pe_ratio": [15.0, 25.0, 12.0, None, 18.0],
        "operating_cash_flow": [15e6, 8e6, 20e6, -1e6, 60e6],
        "last_fiscal_date": [
            date.today() - timedelta(days=30),
            date.today() - timedelta(days=60),
            date.today() - timedelta(days=90),
            date.today() - timedelta(days=45),
            date.today() - timedelta(days=15),
        ],
    })


@pytest.fixture
def sample_enriched(sample_universe, sample_fundamentals):
    """Universe merged with fundamentals."""
    return sample_universe.merge(sample_fundamentals, on="ticker", how="left")


@pytest.fixture
def sample_momentum():
    """Sample momentum data matching sample_universe tickers."""
    return pd.DataFrame({
        "ticker": ["ACME", "BETA", "GAMA", "DELT", "EPSI"],
        "roc_6m": [25.0, 15.0, -5.0, 40.0, 10.0],
        "roc_1m": [8.0, 3.0, -2.0, 20.0, 5.0],
        "sector_roc_6m": [18.0, 12.0, 8.0, 18.0, 15.0],
        "relative_strength": [7.0, 3.0, -13.0, 22.0, -5.0],
        "momentum_score": [25.0, 15.0, -5.0, 20.0, 10.0],  # DELT penalized (top 10% 1m)
    })


@pytest.fixture
def sample_insider():
    """Sample insider buying data."""
    return pd.DataFrame({
        "ticker": ["ACME", "BETA", "GAMA", "DELT", "EPSI"],
        "insider_score": [45.0, 0.0, 20.0, 60.0, 10.0],
    })


@pytest.fixture
def sample_form4_transactions():
    """Sample Form 4 transactions DataFrame."""
    return pd.DataFrame({
        "filed_date": [
            date.today() - timedelta(days=10),
            date.today() - timedelta(days=20),
            date.today() - timedelta(days=5),
            date.today() - timedelta(days=15),
        ],
        "transaction_date": [
            date.today() - timedelta(days=12),
            date.today() - timedelta(days=22),
            date.today() - timedelta(days=7),
            date.today() - timedelta(days=17),
        ],
        "insider_name": ["John CEO", "Jane CFO", "Bob Director", "John CEO"],
        "insider_title": [
            "Chief Executive Officer",
            "Chief Financial Officer",
            "Director",
            "Chief Executive Officer",
        ],
        "transaction_type": ["P", "P", "P", "S"],
        "shares": [10000, 5000, 2000, -3000],
        "price": [15.50, 15.75, 16.00, 16.25],
        "shares_after": [50000, 25000, 12000, 47000],
        "is_direct": [True, True, True, True],
    })


@pytest.fixture
def sample_price_history():
    """Sample 1-year price history DataFrame."""
    dates = pd.date_range(end=date.today(), periods=252, freq="B")
    n = len(dates)
    # Simulate upward trending stock
    import numpy as np
    np.random.seed(42)
    prices = 10.0 * (1 + np.random.normal(0.001, 0.02, n)).cumprod()
    volume = np.random.randint(100_000, 1_000_000, n)

    return pd.DataFrame({
        "Open": prices * 0.99,
        "High": prices * 1.01,
        "Low": prices * 0.98,
        "Close": prices,
        "Volume": volume,
    }, index=dates)


@pytest.fixture
def sample_ranked_df(sample_enriched, sample_momentum, sample_insider):
    """Fully scored and ranked DataFrame."""
    df = sample_enriched.merge(sample_momentum, on="ticker", how="left")
    df = df.merge(sample_insider, on="ticker", how="left")
    df["momentum_rank"] = [80, 60, 10, 90, 40]
    df["quality_score"] = [65, 70, 45, 30, 75]
    df["quality_rank"] = [60, 70, 40, 20, 80]
    df["insider_rank"] = [70, 0, 40, 90, 20]
    df["composite_score"] = [72.0, 48.0, 28.0, 68.0, 48.0]
    df["rank"] = [1, 3, 5, 2, 3]
    return df.sort_values("composite_score", ascending=False).reset_index(drop=True)


# ---------------------------------------------------------------------------
# yfinance mock helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_yfinance_info():
    """Load sample yfinance .info response from fixtures."""
    path = FIXTURES_DIR / "sample_yfinance_info.json"
    if path.exists():
        with open(path) as f:
            return json.load(f)
    # Fallback inline fixture
    return {
        "ACME": {
            "shortName": "Acme Corp",
            "longName": "Acme Corporation",
            "marketCap": 500_000_000,
            "averageVolume": 200_000,
            "currentPrice": 25.00,
            "regularMarketPrice": 25.00,
            "sector": "Technology",
            "industry": "Software",
            "exchange": "NMS",
            "quoteType": "EQUITY",
            "totalRevenue": 100_000_000,
            "revenueGrowth": 0.15,
            "operatingMargins": 0.12,
            "debtToEquity": 50.0,
            "freeCashflow": 10_000_000,
            "trailingPE": 15.0,
            "operatingCashflow": 15_000_000,
            "mostRecentQuarter": 1711929600,
        },
    }
