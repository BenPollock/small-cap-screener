"""Unit tests for fundamentals fetching and quality filtering."""

from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from src.fundamentals import _clean_numeric, _fetch_fundamentals, apply_quality_filters, enrich_fundamentals


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


class TestCleanNumeric:
    """Tests for _clean_numeric helper."""

    def test_returns_valid_numbers(self):
        assert _clean_numeric(15.0) == 15.0
        assert _clean_numeric(0) == 0
        assert _clean_numeric(-3.5) == -3.5

    def test_returns_none_for_none(self):
        assert _clean_numeric(None) is None

    def test_returns_none_for_string_infinity(self):
        """The actual bug: yfinance returns 'Infinity' as a string."""
        assert _clean_numeric("Infinity") is None

    def test_returns_none_for_other_strings(self):
        assert _clean_numeric("N/A") is None
        assert _clean_numeric("NaN") is None

    def test_returns_none_for_float_inf(self):
        assert _clean_numeric(float("inf")) is None
        assert _clean_numeric(float("-inf")) is None

    def test_returns_none_for_float_nan(self):
        assert _clean_numeric(float("nan")) is None


class TestFetchFundamentalsInfinityHandling:
    """Tests that _fetch_fundamentals sanitizes non-numeric values from yfinance."""

    @patch("src.fundamentals.yf.Ticker")
    def test_infinity_pe_ratio_becomes_none(self, mock_ticker_cls):
        """PE ratio of 'Infinity' from yfinance should become None."""
        mock_ticker = MagicMock()
        mock_ticker.info = {
            "totalRevenue": 100_000_000,
            "revenueGrowth": 0.15,
            "operatingMargins": 0.12,
            "debtToEquity": 50.0,
            "freeCashflow": 10_000_000,
            "trailingPE": "Infinity",
            "operatingCashflow": 15_000_000,
            "mostRecentQuarter": None,
        }
        mock_ticker_cls.return_value = mock_ticker

        result = _fetch_fundamentals("ACME")
        assert result["pe_ratio"] is None
        # Other numeric fields should be fine
        assert result["revenue_ttm"] == 100_000_000

    @patch("src.fundamentals.yf.Ticker")
    def test_inf_float_pe_ratio_becomes_none(self, mock_ticker_cls):
        """PE ratio of float('inf') from yfinance should become None."""
        mock_ticker = MagicMock()
        mock_ticker.info = {
            "totalRevenue": 100_000_000,
            "revenueGrowth": 0.15,
            "operatingMargins": 0.12,
            "debtToEquity": 50.0,
            "freeCashflow": 10_000_000,
            "trailingPE": float("inf"),
            "operatingCashflow": 15_000_000,
            "mostRecentQuarter": None,
        }
        mock_ticker_cls.return_value = mock_ticker

        result = _fetch_fundamentals("ACME")
        assert result["pe_ratio"] is None

    @patch("src.fundamentals.yf.Ticker")
    def test_parquet_serialization_with_mixed_pe_values(self, mock_ticker_cls):
        """DataFrame with cleaned infinity values should serialize to parquet without error."""
        mock_ticker = MagicMock()

        # First call returns normal PE, second returns 'Infinity'
        def make_info(pe):
            return {
                "totalRevenue": 100_000_000,
                "revenueGrowth": 0.15,
                "operatingMargins": 0.12,
                "debtToEquity": 50.0,
                "freeCashflow": 10_000_000,
                "trailingPE": pe,
                "operatingCashflow": 15_000_000,
                "mostRecentQuarter": None,
            }

        results = []
        for pe_val in [15.0, "Infinity", float("inf"), None]:
            mock_ticker.info = make_info(pe_val)
            mock_ticker_cls.return_value = mock_ticker
            results.append(_fetch_fundamentals("TEST"))

        df = pd.DataFrame(results)
        # This is the exact operation that was failing
        df.to_parquet("/dev/null", index=False)


class TestApplyQualityFilters:
    """Tests for quality filtering logic."""

    def test_filters_negative_growth(self):
        """Should filter out tickers with growth below -5% threshold."""
        df = pd.DataFrame({
            "ticker": ["GROW", "MILD", "SHRK"],
            "revenue_growth_yoy": [0.10, -0.03, -0.20],
            "operating_cash_flow": [10e6, 10e6, 10e6],
            "debt_to_equity": [0.5, 0.5, 0.5],
        })

        result = apply_quality_filters(df)
        assert len(result) == 2
        assert set(result["ticker"]) == {"GROW", "MILD"}

    def test_relaxes_filters_if_too_few_survivors(self):
        """Should progressively relax thresholds when < 100 tickers survive."""
        # Create 150 tickers all at -8% growth — fails default (-5%) but
        # passes tier 1 relaxation (-10%)
        tickers = [f"T{i:03d}" for i in range(150)]
        df = pd.DataFrame({
            "ticker": tickers,
            "revenue_growth_yoy": [-0.08] * 150,
            "operating_cash_flow": [10e6] * 150,
            "debt_to_equity": [0.5] * 150,
        })

        result = apply_quality_filters(df)
        # Tier 1 relaxation (>-10%) should keep all 150
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
        """Should keep tickers with all-NaN fundamentals (missing != bad)."""
        df = pd.DataFrame({
            "ticker": ["NAN"],
            "revenue_growth_yoy": [None],
            "operating_cash_flow": [None],
            "debt_to_equity": [None],
        })

        result = apply_quality_filters(df)
        # NaN values are kept — missing data is not penalized
        assert len(result) == 1

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
