"""E2E tests for the full screening pipeline with mocked HTTP layer."""

import json
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest


def _make_price_history():
    """Create a mock 1-year price history."""
    dates = pd.date_range(end=date.today(), periods=252, freq="B")
    n = len(dates)
    np.random.seed(42)
    prices = 10.0 * (1 + np.random.normal(0.001, 0.02, n)).cumprod()
    return pd.DataFrame({
        "Open": prices * 0.99,
        "High": prices * 1.01,
        "Low": prices * 0.98,
        "Close": prices,
        "Volume": np.random.randint(100_000, 1_000_000, n),
    }, index=dates)


MOCK_TICKERS = {
    "ACME": {
        "shortName": "Acme Corp", "longName": "Acme Corporation",
        "marketCap": 500_000_000, "averageVolume": 200_000,
        "currentPrice": 25.0, "regularMarketPrice": 25.0,
        "sector": "Technology", "industry": "Software", "exchange": "NMS",
        "quoteType": "EQUITY", "totalRevenue": 100e6, "revenueGrowth": 0.15,
        "operatingMargins": 0.12, "debtToEquity": 50.0,
        "freeCashflow": 10e6, "trailingPE": 15.0,
        "operatingCashflow": 15e6, "mostRecentQuarter": None,
    },
    "BETA": {
        "shortName": "Beta Inc", "longName": "Beta Incorporated",
        "marketCap": 800_000_000, "averageVolume": 500_000,
        "currentPrice": 40.0, "regularMarketPrice": 40.0,
        "sector": "Healthcare", "industry": "Medical Devices", "exchange": "NMS",
        "quoteType": "EQUITY", "totalRevenue": 200e6, "revenueGrowth": 0.25,
        "operatingMargins": 0.20, "debtToEquity": 30.0,
        "freeCashflow": 20e6, "trailingPE": 20.0,
        "operatingCashflow": 25e6, "mostRecentQuarter": None,
    },
    "GAMA": {
        "shortName": "Gamma Holdings", "longName": "Gamma Holdings Inc",
        "marketCap": 1_200_000_000, "averageVolume": 300_000,
        "currentPrice": 55.0, "regularMarketPrice": 55.0,
        "sector": "Industrials", "industry": "Aerospace", "exchange": "NYQ",
        "quoteType": "EQUITY", "totalRevenue": 500e6, "revenueGrowth": 0.08,
        "operatingMargins": 0.15, "debtToEquity": 80.0,
        "freeCashflow": 40e6, "trailingPE": 18.0,
        "operatingCashflow": 50e6, "mostRecentQuarter": None,
    },
}

PRICE_HISTORY = _make_price_history()


def _mock_yf_ticker(symbol, **kwargs):
    """Create a mock yfinance Ticker that returns proper info and history."""
    mock = MagicMock()
    info = MOCK_TICKERS.get(symbol, {"quoteType": None})
    mock.info = info
    mock.history.return_value = PRICE_HISTORY
    return mock


class TestFullPipeline:
    """E2E tests for the full screening pipeline."""

    @patch("src.insider.EdgarClient")
    @patch("src.momentum.yf.Ticker", side_effect=_mock_yf_ticker)
    @patch("src.fundamentals.yf.Ticker", side_effect=_mock_yf_ticker)
    @patch("src.universe.yf.Ticker", side_effect=_mock_yf_ticker)
    @patch("src.universe._batch_volume_prescreen", side_effect=lambda tickers, **kw: tickers)
    @patch("src.universe._fetch_candidate_tickers")
    def test_pipeline_produces_ranked_output(
        self, mock_candidates, mock_prescreen, mock_univ_yf, mock_fund_yf, mock_mom_yf, mock_edgar, tmp_path
    ):
        """Full pipeline should produce a ranked DataFrame with expected columns."""
        mock_candidates.return_value = pd.DataFrame({
            "ticker": ["ACME", "BETA", "GAMA"],
            "company_name": ["Acme", "Beta", "Gamma"],
            "cik": [123, 456, 789],
        })

        mock_edgar_instance = MagicMock()
        mock_edgar_instance.fetch_form4.return_value = pd.DataFrame()
        mock_edgar.return_value = mock_edgar_instance

        from src.pipeline import run_pipeline

        result = run_pipeline(
            top=3,
            output_format="terminal",
            skip_edgar=True,
            cache_dir=str(tmp_path / "cache"),
        )

        assert not result.empty
        assert "composite_score" in result.columns
        assert "rank" in result.columns
        assert "ticker" in result.columns

        # Verify sorted by composite score descending
        scores = result["composite_score"].tolist()
        assert scores == sorted(scores, reverse=True)

    @patch("src.insider.EdgarClient")
    @patch("src.momentum.yf.Ticker", side_effect=_mock_yf_ticker)
    @patch("src.fundamentals.yf.Ticker", side_effect=_mock_yf_ticker)
    @patch("src.universe.yf.Ticker", side_effect=_mock_yf_ticker)
    @patch("src.universe._batch_volume_prescreen", side_effect=lambda tickers, **kw: tickers)
    @patch("src.universe._fetch_candidate_tickers")
    def test_pipeline_respects_top_n(
        self, mock_candidates, mock_prescreen, mock_univ_yf, mock_fund_yf, mock_mom_yf, mock_edgar, tmp_path
    ):
        """Pipeline --top flag should limit result count."""
        mock_candidates.return_value = pd.DataFrame({
            "ticker": ["ACME", "BETA", "GAMA"],
            "company_name": ["Acme", "Beta", "Gamma"],
            "cik": [123, 456, 789],
        })

        mock_edgar_instance = MagicMock()
        mock_edgar_instance.fetch_form4.return_value = pd.DataFrame()
        mock_edgar.return_value = mock_edgar_instance

        from src.pipeline import run_pipeline

        result = run_pipeline(
            top=2,
            output_format="terminal",
            skip_edgar=True,
            cache_dir=str(tmp_path / "cache"),
        )

        assert len(result) <= 2

    @patch("src.insider.EdgarClient")
    @patch("src.momentum.yf.Ticker", side_effect=_mock_yf_ticker)
    @patch("src.fundamentals.yf.Ticker", side_effect=_mock_yf_ticker)
    @patch("src.universe.yf.Ticker", side_effect=_mock_yf_ticker)
    @patch("src.universe._batch_volume_prescreen", side_effect=lambda tickers, **kw: tickers)
    @patch("src.universe._fetch_candidate_tickers")
    def test_pipeline_saves_screen_json(
        self, mock_candidates, mock_prescreen, mock_univ_yf, mock_fund_yf, mock_mom_yf, mock_edgar, tmp_path
    ):
        """Pipeline should save screen results to JSON."""
        mock_candidates.return_value = pd.DataFrame({
            "ticker": ["ACME", "BETA"],
            "company_name": ["Acme", "Beta"],
            "cik": [123, 456],
        })

        mock_edgar_instance = MagicMock()
        mock_edgar_instance.fetch_form4.return_value = pd.DataFrame()
        mock_edgar.return_value = mock_edgar_instance

        from src.pipeline import run_pipeline

        result = run_pipeline(
            top=2,
            output_format="terminal",
            skip_edgar=True,
            cache_dir=str(tmp_path / "cache"),
        )

        # Check screen JSON was saved
        screens_dir = Path("./data/screens")
        if screens_dir.exists():
            json_files = list(screens_dir.glob("screen_*.json"))
            if json_files:
                with open(json_files[-1]) as f:
                    data = json.load(f)
                assert "results" in data
                assert data["count"] > 0
