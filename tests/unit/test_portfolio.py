"""Unit tests for paper portfolio tracking."""

import json
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from src.portfolio import (
    PORTFOLIO_PATH,
    _compute_spy_return,
    _fetch_current_prices,
    _load_portfolio,
    _save_portfolio,
    show_portfolio,
)


class TestPortfolioStorage:
    """Tests for portfolio load/save."""

    def test_load_empty_portfolio(self, tmp_path, monkeypatch):
        """Should return empty cohorts list when no portfolio exists."""
        monkeypatch.setattr("src.portfolio.PORTFOLIO_PATH", tmp_path / "portfolio.json")
        result = _load_portfolio()
        assert result == {"cohorts": []}

    def test_save_and_load_roundtrip(self, tmp_path, monkeypatch):
        """Should correctly save and reload portfolio data."""
        portfolio_path = tmp_path / "portfolio.json"
        monkeypatch.setattr("src.portfolio.PORTFOLIO_PATH", portfolio_path)

        portfolio = {
            "cohorts": [{
                "date": "2024-01-15",
                "tickers": ["ACME", "BETA"],
                "entry_prices": {"ACME": 25.0, "BETA": 40.0},
                "scores": {"ACME": 72.0, "BETA": 48.0},
            }]
        }

        _save_portfolio(portfolio)
        loaded = _load_portfolio()

        assert len(loaded["cohorts"]) == 1
        assert loaded["cohorts"][0]["tickers"] == ["ACME", "BETA"]


class TestFetchCurrentPrices:
    """Tests for price fetching."""

    @patch("src.portfolio.yf.Ticker")
    def test_fetches_prices(self, mock_ticker_cls):
        """Should fetch current prices for given tickers."""
        mock_ticker = MagicMock()
        mock_ticker.info = {"currentPrice": 30.0}
        mock_ticker_cls.return_value = mock_ticker

        result = _fetch_current_prices(["ACME", "BETA"])

        assert "ACME" in result
        assert result["ACME"] == 30.0

    @patch("src.portfolio.yf.Ticker")
    def test_handles_failure(self, mock_ticker_cls):
        """Should skip tickers that fail to fetch."""
        mock_ticker_cls.side_effect = Exception("Network error")

        result = _fetch_current_prices(["BAD"])
        assert result == {}


class TestComputeSpyReturn:
    """Tests for SPY benchmark return calculation."""

    @patch("src.portfolio.yf.Ticker")
    def test_computes_return(self, mock_ticker_cls):
        """Should correctly compute SPY return between dates."""
        mock_ticker = MagicMock()
        mock_hist = pd.DataFrame({
            "Close": [400.0, 420.0],
        }, index=pd.date_range("2024-01-01", periods=2))
        mock_ticker.history.return_value = mock_hist
        mock_ticker_cls.return_value = mock_ticker

        result = _compute_spy_return(date(2024, 1, 1))

        assert abs(result - 5.0) < 0.1  # (420-400)/400 = 5%

    @patch("src.portfolio.yf.Ticker")
    def test_handles_failure(self, mock_ticker_cls):
        """Should return 0 on failure."""
        mock_ticker_cls.side_effect = Exception("Error")
        result = _compute_spy_return(date(2024, 1, 1))
        assert result == 0.0


class TestShowPortfolio:
    """Tests for portfolio display."""

    def test_no_cohorts_message(self, tmp_path, monkeypatch, capsys):
        """Should show message when no cohorts logged."""
        monkeypatch.setattr("src.portfolio.PORTFOLIO_PATH", tmp_path / "portfolio.json")
        show_portfolio()
        captured = capsys.readouterr()
        assert "No cohorts logged" in captured.out
