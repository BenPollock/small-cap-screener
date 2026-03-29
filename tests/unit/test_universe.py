"""Unit tests for universe construction and filtering."""

import json
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
import responses

from src.universe import (
    EDGAR_TICKERS_URL,
    _apply_filters,
    _fetch_candidate_tickers,
    _fetch_ticker_info,
    get_universe,
)


class TestFetchCandidateTickers:
    """Tests for fetching candidate tickers from SEC EDGAR."""

    @responses.activate
    def test_fetches_from_sec(self):
        """Should fetch tickers from SEC EDGAR endpoint."""
        mock_data = {
            "0": {"cik_str": 123, "ticker": "ACME", "title": "Acme Corp"},
            "1": {"cik_str": 456, "ticker": "BETA", "title": "Beta Inc"},
        }
        responses.add(
            responses.GET,
            EDGAR_TICKERS_URL,
            json=mock_data,
            status=200,
        )

        result = _fetch_candidate_tickers()
        assert len(result) == 2
        assert "ACME" in result["ticker"].values
        assert "BETA" in result["ticker"].values

    @responses.activate
    def test_filters_special_characters(self):
        """Should exclude tickers with special chars (warrants, units)."""
        mock_data = {
            "0": {"cik_str": 1, "ticker": "ACME", "title": "Acme"},
            "1": {"cik_str": 2, "ticker": "SPAC+", "title": "SPAC Warrant"},
            "2": {"cik_str": 3, "ticker": "UN-IT", "title": "Unit"},
        }
        responses.add(responses.GET, EDGAR_TICKERS_URL, json=mock_data, status=200)

        result = _fetch_candidate_tickers()
        assert "ACME" in result["ticker"].values
        assert "SPAC+" not in result["ticker"].values
        assert "UN-IT" not in result["ticker"].values

    @responses.activate
    def test_handles_sec_failure(self):
        """Should return empty DataFrame on SEC endpoint failure."""
        responses.add(responses.GET, EDGAR_TICKERS_URL, status=500)

        result = _fetch_candidate_tickers()
        assert isinstance(result, pd.DataFrame)


class TestApplyFilters:
    """Tests for universe filter logic."""

    def test_market_cap_filter(self):
        """Should filter by market cap range."""
        df = pd.DataFrame({
            "ticker": ["TINY", "MID", "BIG"],
            "company_name": ["Tiny", "Mid", "Big"],
            "market_cap": [50_000_000, 500_000_000, 5_000_000_000],
            "avg_dollar_volume": [1_000_000, 1_000_000, 1_000_000],
            "exchange": ["NMS", "NMS", "NMS"],
            "sector": ["Technology", "Technology", "Technology"],
        })

        result = _apply_filters(df, min_mcap=200, max_mcap=2000)
        assert len(result) == 1
        assert result.iloc[0]["ticker"] == "MID"

    def test_market_cap_boundary_inclusive(self):
        """Ticker exactly at boundary should be included."""
        df = pd.DataFrame({
            "ticker": ["EXACT"],
            "company_name": ["Exact"],
            "market_cap": [200_000_000],  # Exactly $200M
            "avg_dollar_volume": [1_000_000],
            "exchange": ["NMS"],
            "sector": ["Technology"],
        })

        result = _apply_filters(df, min_mcap=200, max_mcap=2000)
        assert len(result) == 1

    def test_dollar_volume_filter(self):
        """Should exclude tickers with < $500K daily dollar volume."""
        df = pd.DataFrame({
            "ticker": ["LOW", "HIGH"],
            "company_name": ["Low Vol", "High Vol"],
            "market_cap": [500_000_000, 500_000_000],
            "avg_dollar_volume": [100_000, 1_000_000],
            "exchange": ["NMS", "NMS"],
            "sector": ["Technology", "Technology"],
        })

        result = _apply_filters(df)
        assert len(result) == 1
        assert result.iloc[0]["ticker"] == "HIGH"

    def test_exchange_filter(self):
        """Should only keep NYSE/NASDAQ exchanges."""
        df = pd.DataFrame({
            "ticker": ["NYSE", "OTC"],
            "company_name": ["NYSE Co", "OTC Co"],
            "market_cap": [500_000_000, 500_000_000],
            "avg_dollar_volume": [1_000_000, 1_000_000],
            "exchange": ["NYQ", "PNK"],
            "sector": ["Technology", "Technology"],
        })

        result = _apply_filters(df)
        assert len(result) == 1
        assert result.iloc[0]["ticker"] == "NYSE"

    def test_spac_exclusion(self):
        """Should exclude companies with SPAC-related names."""
        df = pd.DataFrame({
            "ticker": ["REAL", "SPAC"],
            "company_name": ["Real Company", "Acquisition Corp Holdings"],
            "market_cap": [500_000_000, 500_000_000],
            "avg_dollar_volume": [1_000_000, 1_000_000],
            "exchange": ["NMS", "NMS"],
            "sector": ["Technology", "Technology"],
        })

        result = _apply_filters(df)
        assert len(result) == 1
        assert result.iloc[0]["ticker"] == "REAL"

    def test_reit_exclusion(self):
        """Should exclude REITs when include_reits=False."""
        df = pd.DataFrame({
            "ticker": ["TECH", "REIT"],
            "company_name": ["Tech Co", "REIT Trust"],
            "market_cap": [500_000_000, 500_000_000],
            "avg_dollar_volume": [1_000_000, 1_000_000],
            "exchange": ["NMS", "NMS"],
            "sector": ["Technology", "Real Estate"],
        })

        result = _apply_filters(df, include_reits=False)
        assert len(result) == 1
        assert result.iloc[0]["ticker"] == "TECH"

    def test_reit_inclusion(self):
        """Should include REITs when include_reits=True."""
        df = pd.DataFrame({
            "ticker": ["TECH", "REIT"],
            "company_name": ["Tech Co", "REIT Trust"],
            "market_cap": [500_000_000, 500_000_000],
            "avg_dollar_volume": [1_000_000, 1_000_000],
            "exchange": ["NMS", "NMS"],
            "sector": ["Technology", "Real Estate"],
        })

        result = _apply_filters(df, include_reits=True)
        assert len(result) == 2

    def test_adr_exclusion(self):
        """Should exclude ADRs."""
        df = pd.DataFrame({
            "ticker": ["REAL", "FADR"],
            "company_name": ["Real Company", "Foreign ADR Holdings"],
            "market_cap": [500_000_000, 500_000_000],
            "avg_dollar_volume": [1_000_000, 1_000_000],
            "exchange": ["NMS", "NMS"],
            "sector": ["Technology", "Technology"],
        })

        result = _apply_filters(df)
        assert len(result) == 1
        assert result.iloc[0]["ticker"] == "REAL"

    def test_empty_dataframe(self):
        """Should handle empty DataFrame gracefully."""
        result = _apply_filters(pd.DataFrame())
        assert result.empty


class TestGetUniverse:
    """Tests for the main get_universe function."""

    def test_uses_cache_if_available(self, tmp_path):
        """Should load from cache on same-day re-run."""
        from datetime import date

        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        cache_file = cache_dir / f"universe_{date.today().isoformat()}.parquet"

        expected = pd.DataFrame({
            "ticker": ["CACHED"],
            "company_name": ["Cached Co"],
            "market_cap": [500_000_000],
        })
        expected.to_parquet(cache_file, index=False)

        result = get_universe(cache_dir=str(cache_dir))
        assert len(result) == 1
        assert result.iloc[0]["ticker"] == "CACHED"
