"""Unit tests for momentum calculations."""

from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from src.momentum import (
    _apply_reversal_penalty,
    _compute_single_momentum,
    compute_momentum_scores,
)


class TestComputeSingleMomentum:
    """Tests for single-ticker momentum computation."""

    @patch("src.momentum.yf.Ticker")
    def test_computes_6m_roc(self, mock_ticker_cls, sample_price_history):
        """Should correctly compute 6-month ROC."""
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = sample_price_history
        mock_ticker_cls.return_value = mock_ticker

        result = _compute_single_momentum("ACME", "Technology", {"Technology": 10.0})

        assert result["ticker"] == "ACME"
        assert result["roc_6m"] is not None
        assert isinstance(result["roc_6m"], float)

    @patch("src.momentum.yf.Ticker")
    def test_computes_1m_roc(self, mock_ticker_cls, sample_price_history):
        """Should correctly compute 1-month ROC."""
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = sample_price_history
        mock_ticker_cls.return_value = mock_ticker

        result = _compute_single_momentum("ACME", "Technology", {})

        assert result["roc_1m"] is not None
        assert isinstance(result["roc_1m"], float)

    @patch("src.momentum.yf.Ticker")
    def test_computes_relative_strength(self, mock_ticker_cls, sample_price_history):
        """Should compute relative strength vs sector ETF."""
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = sample_price_history
        mock_ticker_cls.return_value = mock_ticker

        sector_rocs = {"Technology": 10.0}
        result = _compute_single_momentum("ACME", "Technology", sector_rocs)

        assert result["relative_strength"] is not None
        assert result["sector_roc_6m"] == 10.0

    @patch("src.momentum.yf.Ticker")
    def test_handles_empty_history(self, mock_ticker_cls):
        """Should return None values for empty price history."""
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = pd.DataFrame()
        mock_ticker_cls.return_value = mock_ticker

        result = _compute_single_momentum("BAD", "Technology", {})

        assert result["roc_6m"] is None
        assert result["roc_1m"] is None

    @patch("src.momentum.yf.Ticker")
    def test_handles_short_history(self, mock_ticker_cls):
        """Should handle history with fewer than 126 days."""
        dates = pd.date_range(end="2024-01-15", periods=50, freq="B")
        short_hist = pd.DataFrame({
            "Close": np.linspace(10, 12, 50),
            "Open": np.linspace(9.9, 11.9, 50),
            "High": np.linspace(10.1, 12.1, 50),
            "Low": np.linspace(9.8, 11.8, 50),
            "Volume": [100000] * 50,
        }, index=dates)

        mock_ticker = MagicMock()
        mock_ticker.history.return_value = short_hist
        mock_ticker_cls.return_value = mock_ticker

        result = _compute_single_momentum("SHORT", "Technology", {})

        # Should still compute 1m ROC even with short history
        assert result["roc_1m"] is not None
        # 6m ROC should use fallback (full available history)
        assert result["roc_6m"] is None  # < 63 days = no fallback either

    @patch("src.momentum.yf.Ticker")
    def test_handles_exception(self, mock_ticker_cls):
        """Should return None values on yfinance exception."""
        mock_ticker_cls.side_effect = Exception("API Error")

        result = _compute_single_momentum("ERR", "Technology", {})
        assert result["roc_6m"] is None


class TestApplyReversalPenalty:
    """Tests for the short-term reversal penalty."""

    def test_penalizes_extended_stocks(self):
        """Should reduce momentum score by 50% for top 10% 1m ROC."""
        df = pd.DataFrame({
            "ticker": [f"T{i}" for i in range(10)],
            "roc_1m": [1, 2, 3, 4, 5, 6, 7, 8, 9, 50],  # T9 is extended
            "momentum_score": [10.0] * 10,
        })

        result = _apply_reversal_penalty(df)

        # T9 (roc_1m=50) should be penalized
        assert result.iloc[9]["momentum_score"] == 5.0
        # Others should be unchanged
        assert result.iloc[0]["momentum_score"] == 10.0

    def test_handles_all_nan(self):
        """Should handle all-NaN 1m ROC gracefully."""
        df = pd.DataFrame({
            "ticker": ["A", "B"],
            "roc_1m": [None, None],
            "momentum_score": [10.0, 20.0],
        })

        result = _apply_reversal_penalty(df)
        assert result.iloc[0]["momentum_score"] == 10.0

    def test_handles_empty_df(self):
        """Should handle empty DataFrame."""
        result = _apply_reversal_penalty(pd.DataFrame())
        assert result.empty


class TestComputeMomentumScores:
    """Tests for the main momentum scoring function."""

    def test_uses_cache_if_available(self, tmp_path, sample_universe):
        """Should load momentum from cache."""
        from datetime import date

        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        cache_file = cache_dir / f"momentum_{date.today().isoformat()}.parquet"

        cached = pd.DataFrame({
            "ticker": ["ACME", "BETA", "GAMA", "DELT", "EPSI"],
            "roc_6m": [10.0, 20.0, -5.0, 15.0, 8.0],
            "roc_1m": [3.0, 5.0, -1.0, 8.0, 2.0],
            "sector_roc_6m": [8.0, 12.0, 6.0, 8.0, 10.0],
            "relative_strength": [2.0, 8.0, -11.0, 7.0, -2.0],
            "momentum_score": [10.0, 20.0, -5.0, 15.0, 8.0],
        })
        cached.to_parquet(cache_file, index=False)

        result = compute_momentum_scores(sample_universe, cache_dir=str(cache_dir))
        assert "roc_6m" in result.columns
