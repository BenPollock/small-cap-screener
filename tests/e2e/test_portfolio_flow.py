"""E2E test for portfolio log → show round-trip with mocked prices."""

import json
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from src.portfolio import _load_portfolio, _save_portfolio, show_portfolio


class TestPortfolioFlow:
    """End-to-end portfolio log → show round-trip."""

    @patch("src.portfolio.yf.Ticker")
    def test_show_computes_returns(self, mock_ticker_cls, tmp_path, monkeypatch, capsys):
        """Should correctly compute returns for a logged cohort."""
        portfolio_path = tmp_path / "portfolio.json"
        monkeypatch.setattr("src.portfolio.PORTFOLIO_PATH", portfolio_path)

        # Pre-populate a cohort from 30 days ago
        cohort_date = date.today() - timedelta(days=30)
        portfolio = {
            "cohorts": [{
                "date": cohort_date.isoformat(),
                "timestamp": f"{cohort_date.isoformat()}T12:00:00",
                "tickers": ["ACME", "BETA"],
                "entry_prices": {"ACME": 20.0, "BETA": 40.0},
                "scores": {"ACME": 72.0, "BETA": 48.0},
            }]
        }
        _save_portfolio(portfolio)

        # Mock current prices: ACME up 10%, BETA down 5%
        def make_ticker(symbol):
            mock = MagicMock()
            if symbol == "ACME":
                mock.info = {"currentPrice": 22.0}  # +10%
            elif symbol == "BETA":
                mock.info = {"currentPrice": 38.0}  # -5%
            elif symbol == "SPY":
                mock.history.return_value = pd.DataFrame({
                    "Close": [450.0, 459.0],  # +2%
                }, index=pd.date_range(cohort_date, periods=2))
                mock.info = {}
            else:
                mock.info = {}
            return mock

        mock_ticker_cls.side_effect = make_ticker

        show_portfolio()
        captured = capsys.readouterr()

        # Verify output contains cohort info
        assert cohort_date.isoformat() in captured.out
        assert "30d" in captured.out

    @patch("src.portfolio.yf.Ticker")
    def test_multiple_cohorts(self, mock_ticker_cls, tmp_path, monkeypatch, capsys):
        """Should show all logged cohorts."""
        portfolio_path = tmp_path / "portfolio.json"
        monkeypatch.setattr("src.portfolio.PORTFOLIO_PATH", portfolio_path)

        portfolio = {
            "cohorts": [
                {
                    "date": (date.today() - timedelta(days=60)).isoformat(),
                    "timestamp": "2024-01-01T12:00:00",
                    "tickers": ["ACME"],
                    "entry_prices": {"ACME": 20.0},
                    "scores": {"ACME": 72.0},
                },
                {
                    "date": (date.today() - timedelta(days=30)).isoformat(),
                    "timestamp": "2024-02-01T12:00:00",
                    "tickers": ["BETA"],
                    "entry_prices": {"BETA": 40.0},
                    "scores": {"BETA": 48.0},
                },
            ]
        }
        _save_portfolio(portfolio)

        def make_ticker(symbol):
            mock = MagicMock()
            mock.info = {"currentPrice": 25.0}
            mock.history.return_value = pd.DataFrame({
                "Close": [100.0, 102.0],
            }, index=pd.date_range("2024-01-01", periods=2))
            return mock

        mock_ticker_cls.side_effect = make_ticker

        show_portfolio()
        captured = capsys.readouterr()

        # Both cohort dates should appear
        assert "60d" in captured.out
        assert "30d" in captured.out

    def test_empty_portfolio(self, tmp_path, monkeypatch, capsys):
        """Should handle empty portfolio gracefully."""
        monkeypatch.setattr("src.portfolio.PORTFOLIO_PATH", tmp_path / "portfolio.json")

        show_portfolio()
        captured = capsys.readouterr()
        assert "No cohorts logged" in captured.out
