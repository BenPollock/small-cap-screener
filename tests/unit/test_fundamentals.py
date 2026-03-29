"""Unit tests for fundamentals fetching and quality filtering."""

from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from src.fundamentals import _fetch_fundamentals, apply_quality_filters, enrich_fundamentals


class TestFetchFundamentals:
    """Tests for fetching fundamental data from yfinance."""

    @patch("src.fundamentals.yf.Ticker")
    def test_fetches_all_fields(self, mock_ticker_cls):
        """Should extract all fundamental fields from yfinance info."""
        mock_ticker = MagicMock()
        mock_ticker.info = {
            "totalRevenue": 100_000_000,
            "revenueGrowth": 0.15,
            "operatingMargins": 0.12,
            "debtToEquity": 50.0,
            "freeCashflow": 10_000_000,
            "trailingPE": 15.0,
            "operatingCashflow": 15_000_000,
            "mostRecentQuarter": None,
        }
        mock_ticker_cls.return_value = mock_ticker

        result = _fetch_fundamentals("ACME")

        assert result is not None
        assert result["ticker"] == "ACME"
        assert result["revenue_ttm"] == 100_000_000
        assert result["revenue_growth_yoy"] == 0.15
        assert result["operating_margin"] == 0.12
        assert result["debt_to_equity"] == 0.50  # Converted from percentage
        assert result["free_cash_flow"] == 10_000_000
        assert result["pe_ratio"] == 15.0

    @patch("src.fundamentals.yf.Ticker")
    def test_handles_missing_info(self, mock_ticker_cls):
        """Should return None when yfinance returns empty info."""
        mock_ticker = MagicMock()
        mock_ticker.info = {}
        mock_ticker_cls.return_value = mock_ticker

        result = _fetch_fundamentals("BAD")
        assert result is None

    @patch("src.fundamentals.yf.Ticker")
    def test_handles_exception(self, mock_ticker_cls):
        """Should return None on exception."""
        mock_ticker_cls.side_effect = Exception("Network error")

        result = _fetch_fundamentals("ERR")
        assert result is None


class TestApplyQualityFilters:
    """Tests for quality filtering logic."""

    def test_filters_negative_growth(self):
        """Should filter out tickers with negative revenue growth."""
        df = pd.DataFrame({
            "ticker": ["GROW", "SHRK"],
            "revenue_growth_yoy": [0.10, -0.20],
            "operating_cash_flow": [10e6, 10e6],
            "debt_to_equity": [0.5, 0.5],
        })

        result = apply_quality_filters(df)
        assert len(result) == 1
        assert result.iloc[0]["ticker"] == "GROW"

    def test_relaxes_growth_filter_if_too_aggressive(self):
        """Should relax growth filter when < 100 survivors from large input."""
        # Create 150 tickers, 80 with slightly negative growth
        tickers = [f"T{i:03d}" for i in range(150)]
        growth = [0.05] * 70 + [-0.05] * 80
        df = pd.DataFrame({
            "ticker": tickers,
            "revenue_growth_yoy": growth,
            "operating_cash_flow": [10e6] * 150,
            "debt_to_equity": [0.5] * 150,
        })

        result = apply_quality_filters(df)
        # With relaxed filter (>-10%), all 150 should survive
        assert len(result) == 150

    def test_filters_high_debt(self):
        """Should filter out tickers with debt/equity >= 2.0."""
        df = pd.DataFrame({
            "ticker": ["LOW", "HIGH"],
            "revenue_growth_yoy": [0.10, 0.10],
            "operating_cash_flow": [10e6, 10e6],
            "debt_to_equity": [1.0, 3.0],
        })

        result = apply_quality_filters(df)
        assert len(result) == 1
        assert result.iloc[0]["ticker"] == "LOW"

    def test_handles_nan_values(self):
        """Should not crash on NaN fundamental values."""
        df = pd.DataFrame({
            "ticker": ["NAN"],
            "revenue_growth_yoy": [None],
            "operating_cash_flow": [None],
            "debt_to_equity": [None],
        })

        result = apply_quality_filters(df)
        # NaN revenue growth is filtered out
        assert len(result) == 0

    def test_keeps_missing_cash_flow(self):
        """Should keep tickers with missing cash flow data."""
        df = pd.DataFrame({
            "ticker": ["MISS"],
            "revenue_growth_yoy": [0.10],
            "operating_cash_flow": [None],
            "debt_to_equity": [0.5],
        })

        result = apply_quality_filters(df)
        assert len(result) == 1

    def test_empty_dataframe(self):
        """Should handle empty input gracefully."""
        result = apply_quality_filters(pd.DataFrame())
        assert result.empty


class TestEnrichFundamentals:
    """Tests for the enrich_fundamentals function."""

    def test_uses_cache_if_available(self, tmp_path, sample_universe):
        """Should load fundamentals from cache."""
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        cache_file = cache_dir / f"fundamentals_{date.today().isoformat()}.parquet"

        cached_data = pd.DataFrame({
            "ticker": ["ACME", "BETA", "GAMA", "DELT", "EPSI"],
            "revenue_ttm": [100e6, 50e6, 200e6, 30e6, 500e6],
        })
        cached_data.to_parquet(cache_file, index=False)

        result = enrich_fundamentals(sample_universe, cache_dir=str(cache_dir))
        assert "revenue_ttm" in result.columns
        assert len(result) == 5
