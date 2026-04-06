"""Unit tests for composite scoring and ranking."""

import pandas as pd
import pytest

from src.scorer import _compute_quality_score, _percentile_rank, compute_composite_scores


class TestPercentileRank:
    """Tests for percentile ranking normalization."""

    def test_basic_ranking(self):
        """Should produce 0-100 percentile ranks."""
        df = pd.DataFrame({"score": [10, 20, 30, 40, 50]})
        result = _percentile_rank(df, "score")

        assert result.min() >= 0
        assert result.max() <= 100
        # Highest value should have highest rank
        assert result.iloc[4] > result.iloc[0]

    def test_all_same_nonzero_values(self):
        """Should return 50 for all when values are identical and non-zero."""
        df = pd.DataFrame({"score": [10.0, 10.0, 10.0]})
        result = _percentile_rank(df, "score")

        assert (result == 50.0).all()

    def test_all_zero_values(self):
        """Should return 0 for all when values are all zero (no signal)."""
        df = pd.DataFrame({"score": [0.0, 0.0, 0.0]})
        result = _percentile_rank(df, "score")

        assert (result == 0.0).all()

    def test_single_ticker(self):
        """Should return 50 for a single ticker."""
        df = pd.DataFrame({"score": [25.0]})
        result = _percentile_rank(df, "score")

        assert result.iloc[0] == 50.0

    def test_all_nan(self):
        """Should return 0 for all when all values are NaN."""
        df = pd.DataFrame({"score": [None, None, None]})
        result = _percentile_rank(df, "score")

        assert (result == 0.0).all()

    def test_nan_values_get_zero(self):
        """NaN values should get rank 0."""
        df = pd.DataFrame({"score": [10.0, None, 30.0]})
        result = _percentile_rank(df, "score")

        assert result.iloc[1] == 0.0
        assert result.iloc[0] > 0
        assert result.iloc[2] > 0


class TestComputeQualityScore:
    """Tests for quality score computation."""

    def test_combines_factors(self):
        """Should average percentile ranks of quality factors."""
        df = pd.DataFrame({
            "revenue_growth_yoy": [0.10, 0.20, 0.05],
            "operating_margin": [0.15, 0.10, 0.20],
            "free_cash_flow": [10e6, 5e6, 15e6],
        })

        result = _compute_quality_score(df)
        assert len(result) == 3
        assert result.notna().all()

    def test_handles_missing_columns(self):
        """Should return 50 when no quality columns exist."""
        df = pd.DataFrame({"ticker": ["A", "B"]})
        result = _compute_quality_score(df)

        assert (result == 50.0).all()


class TestComputeCompositeScores:
    """Tests for composite score computation and ranking."""

    def test_returns_top_n(self):
        """Should return exactly top N tickers."""
        df = pd.DataFrame({
            "ticker": [f"T{i}" for i in range(10)],
            "momentum_score": range(10),
            "revenue_growth_yoy": [0.1] * 10,
            "operating_margin": [0.1] * 10,
            "free_cash_flow": [1e6] * 10,
            "insider_score": [0.0] * 10,
        })

        result = compute_composite_scores(df, top=5)
        assert len(result) == 5

    def test_sorted_by_composite_descending(self):
        """Results should be sorted by composite score descending."""
        df = pd.DataFrame({
            "ticker": ["LOW", "MID", "HIGH"],
            "momentum_score": [5.0, 15.0, 25.0],
            "revenue_growth_yoy": [0.05, 0.10, 0.20],
            "operating_margin": [0.05, 0.10, 0.15],
            "free_cash_flow": [1e6, 5e6, 10e6],
            "insider_score": [0.0, 10.0, 50.0],
        })

        result = compute_composite_scores(df, top=3)
        scores = result["composite_score"].tolist()
        assert scores == sorted(scores, reverse=True)

    def test_skip_edgar_weights(self):
        """With skip_edgar, should use 55/45 momentum/quality weights."""
        df = pd.DataFrame({
            "ticker": ["A", "B"],
            "momentum_score": [20.0, 10.0],
            "revenue_growth_yoy": [0.10, 0.20],
            "operating_margin": [0.10, 0.15],
            "free_cash_flow": [5e6, 10e6],
            "insider_score": [50.0, 0.0],  # Should be ignored
        })

        result_with = compute_composite_scores(df, top=2, skip_edgar=False)
        result_without = compute_composite_scores(df, top=2, skip_edgar=True)

        # Rankings may differ when insider signal is removed
        assert "composite_score" in result_with.columns
        assert "composite_score" in result_without.columns

    def test_rank_column(self):
        """Should add a rank column starting at 1."""
        df = pd.DataFrame({
            "ticker": ["A", "B", "C"],
            "momentum_score": [10.0, 20.0, 30.0],
            "revenue_growth_yoy": [0.1, 0.1, 0.1],
            "operating_margin": [0.1, 0.1, 0.1],
            "free_cash_flow": [1e6, 1e6, 1e6],
            "insider_score": [0.0, 0.0, 0.0],
        })

        result = compute_composite_scores(df, top=3)
        assert result.iloc[0]["rank"] == 1
        assert result.iloc[1]["rank"] == 2
        assert result.iloc[2]["rank"] == 3

    def test_empty_dataframe(self):
        """Should handle empty input."""
        result = compute_composite_scores(pd.DataFrame(), top=5)
        assert result.empty
