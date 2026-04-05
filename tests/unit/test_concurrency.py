"""Unit tests for concurrency features: thread-safe rate limiter, progressive caching,
exchange pre-filtering, batch volume prescreen, and --workers CLI flag."""

import threading
import time
from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
import responses

from edgar.rate_limiter import RateLimiter


class TestThreadSafeRateLimiter:
    """Tests for the thread-safe token-bucket rate limiter."""

    def test_enforces_minimum_interval(self):
        """Consecutive calls should be spaced by at least the interval."""
        limiter = RateLimiter(max_per_second=10)

        start = time.time()
        limiter.wait()
        limiter.wait()
        elapsed = time.time() - start

        assert elapsed >= 0.1  # 1/10 sec interval

    def test_thread_safety(self):
        """Multiple threads sharing a limiter should not violate the rate limit."""
        limiter = RateLimiter(max_per_second=10)
        timestamps = []
        lock = threading.Lock()

        def worker():
            limiter.wait()
            with lock:
                timestamps.append(time.time())

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        timestamps.sort()
        # Each consecutive pair should be >= 0.09s apart (allow tiny float imprecision)
        for i in range(1, len(timestamps)):
            gap = timestamps[i] - timestamps[i - 1]
            assert gap >= 0.08, f"Gap {gap:.4f}s between requests {i-1} and {i} is too small"

    def test_no_unnecessary_wait_on_first_call(self):
        """First call should not block."""
        limiter = RateLimiter(max_per_second=10)

        start = time.time()
        limiter.wait()
        elapsed = time.time() - start

        assert elapsed < 0.05  # Should be near-instant


class TestExchangePreFilter:
    """Tests for the EDGAR exchange endpoint pre-filtering."""

    @responses.activate
    def test_filters_by_exchange(self):
        """Should only keep NYSE/Nasdaq tickers."""
        from src.universe import EDGAR_TICKERS_EXCHANGE_URL, _fetch_with_exchange_filter

        mock_data = {
            "fields": ["cik", "name", "ticker", "exchange"],
            "data": [
                [1, "NYSE Co", "AAAA", "NYSE"],
                [2, "Nasdaq Co", "BBBB", "Nasdaq"],
                [3, "OTC Co", "CCCC", "OTC"],
                [4, "BATS Co", "DDDD", "BATS"],
            ],
        }
        responses.add(
            responses.GET, EDGAR_TICKERS_EXCHANGE_URL, json=mock_data, status=200,
        )

        result = _fetch_with_exchange_filter()

        assert result is not None
        assert set(result["ticker"].tolist()) == {"AAAA", "BBBB"}

    @responses.activate
    def test_returns_none_on_failure(self):
        """Should return None if exchange endpoint is unavailable."""
        from src.universe import EDGAR_TICKERS_EXCHANGE_URL, _fetch_with_exchange_filter

        responses.add(responses.GET, EDGAR_TICKERS_EXCHANGE_URL, status=500)

        result = _fetch_with_exchange_filter()
        assert result is None

    @responses.activate
    def test_drops_exchange_column(self):
        """Should drop exchange column (yfinance enrichment adds the canonical one)."""
        from src.universe import EDGAR_TICKERS_EXCHANGE_URL, _fetch_with_exchange_filter

        mock_data = {
            "fields": ["cik", "name", "ticker", "exchange"],
            "data": [[1, "Test Co", "TEST", "NYSE"]],
        }
        responses.add(
            responses.GET, EDGAR_TICKERS_EXCHANGE_URL, json=mock_data, status=200,
        )

        result = _fetch_with_exchange_filter()
        assert "exchange" not in result.columns


class TestBatchVolumePrescreen:
    """Tests for the yf.download() batch volume prescreen."""

    @patch("src.universe.yf.download")
    def test_passes_high_volume_tickers(self, mock_download):
        """Should pass tickers with sufficient dollar volume."""
        from src.universe import _batch_volume_prescreen

        dates = pd.date_range("2024-01-10", periods=5, freq="B")
        mock_download.return_value = pd.DataFrame({
            ("GOOD", "Close"): [20.0, 21.0, 22.0, 23.0, 24.0],
            ("GOOD", "Volume"): [100_000, 100_000, 100_000, 100_000, 100_000],
            ("BAD", "Close"): [1.0, 1.0, 1.0, 1.0, 1.0],
            ("BAD", "Volume"): [100, 100, 100, 100, 100],
        }, index=dates)
        mock_download.return_value.columns = pd.MultiIndex.from_tuples([
            ("GOOD", "Close"), ("GOOD", "Volume"),
            ("BAD", "Close"), ("BAD", "Volume"),
        ])

        result = _batch_volume_prescreen(["GOOD", "BAD"])

        assert "GOOD" in result
        assert "BAD" not in result

    @patch("src.universe.yf.download")
    def test_passes_all_on_exception(self, mock_download):
        """Should pass all tickers through if yf.download() raises."""
        from src.universe import _batch_volume_prescreen

        mock_download.side_effect = Exception("Network error")

        result = _batch_volume_prescreen(["A", "B", "C"])
        assert set(result) == {"A", "B", "C"}


class TestProgressiveCaching:
    """Tests for progressive caching / crash resilience."""

    @patch("src.universe.yf.Ticker")
    def test_enrichment_resumes_from_partial(self, mock_ticker_cls, tmp_path):
        """Should resume from partial cache instead of re-fetching."""
        from src.universe import _enrich_with_yfinance

        # Write a partial cache with one ticker already done
        partial_path = tmp_path / f"_enrichment_partial_{date.today().isoformat()}.parquet"
        existing = pd.DataFrame([{
            "ticker": "DONE",
            "company_name": "Done Co",
            "market_cap": 500e6,
            "avg_volume": 100_000,
            "avg_dollar_volume": 1e6,
            "sector": "Tech",
            "industry": "Software",
            "exchange": "NMS",
        }])
        existing.to_parquet(partial_path, index=False)

        # Mock yfinance for the remaining ticker
        mock_ticker = MagicMock()
        mock_ticker.info = {
            "quoteType": "EQUITY", "marketCap": 600e6,
            "averageVolume": 200_000, "currentPrice": 30.0,
            "longName": "New Co", "sector": "Healthcare",
            "industry": "Biotech", "exchange": "NYQ",
        }
        mock_ticker_cls.return_value = mock_ticker

        candidates = pd.DataFrame({"ticker": ["DONE", "NEW"]})
        result = _enrich_with_yfinance(
            candidates, max_workers=2, cache_dir=str(tmp_path),
        )

        # Should have both tickers
        assert len(result) == 2
        assert "DONE" in result["ticker"].values
        assert "NEW" in result["ticker"].values

        # Partial file should be cleaned up
        assert not partial_path.exists()

    @patch("src.fundamentals.yf.Ticker")
    def test_fundamentals_resumes_from_partial(self, mock_ticker_cls, tmp_path):
        """Should resume fundamentals from partial cache."""
        from src.fundamentals import enrich_fundamentals

        partial_path = tmp_path / f"_fundamentals_partial_{date.today().isoformat()}.parquet"
        existing = pd.DataFrame([{
            "ticker": "DONE",
            "revenue_ttm": 100e6,
            "revenue_growth_yoy": 0.15,
            "operating_margin": 0.12,
            "debt_to_equity": 0.5,
            "free_cash_flow": 10e6,
            "pe_ratio": 15.0,
            "operating_cash_flow": 15e6,
            "last_fiscal_date": None,
        }])
        existing.to_parquet(partial_path, index=False)

        # Mock for NEW ticker
        mock_ticker = MagicMock()
        mock_ticker.info = {
            "totalRevenue": 200e6, "revenueGrowth": 0.20,
            "operatingMargins": 0.15, "debtToEquity": 40.0,
            "freeCashflow": 20e6, "trailingPE": 18.0,
            "operatingCashflow": 25e6, "mostRecentQuarter": None,
        }
        mock_ticker_cls.return_value = mock_ticker

        universe = pd.DataFrame({
            "ticker": ["DONE", "NEW"],
            "company_name": ["Done Co", "New Co"],
        })
        result = enrich_fundamentals(universe, cache_dir=str(tmp_path))

        # Both tickers should be present
        assert len(result) == 2
        assert "DONE" in result["ticker"].values
        assert "NEW" in result["ticker"].values

        # Partial file should be cleaned up, final cache should exist
        assert not partial_path.exists()
        final_cache = tmp_path / f"fundamentals_{date.today().isoformat()}.parquet"
        assert final_cache.exists()


class TestWorkersCliFlag:
    """Tests for the --workers CLI flag."""

    @patch("src.pipeline.run_pipeline")
    def test_workers_flag_default(self, mock_pipeline):
        """--workers defaults to 8."""
        from click.testing import CliRunner
        from src.cli import cli

        mock_pipeline.return_value = pd.DataFrame()
        runner = CliRunner()
        result = runner.invoke(cli, ["run"])

        assert result.exit_code == 0
        assert mock_pipeline.call_args.kwargs["max_workers"] == 8

    @patch("src.pipeline.run_pipeline")
    def test_workers_flag_custom(self, mock_pipeline):
        """--workers should pass custom value."""
        from click.testing import CliRunner
        from src.cli import cli

        mock_pipeline.return_value = pd.DataFrame()
        runner = CliRunner()
        result = runner.invoke(cli, ["run", "--workers", "4"])

        assert result.exit_code == 0
        assert mock_pipeline.call_args.kwargs["max_workers"] == 4

    def test_workers_flag_in_help(self):
        """--workers should appear in help text."""
        from click.testing import CliRunner
        from src.cli import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["run", "--help"])
        assert "--workers" in result.output
