"""Unit tests for insider signal computation and Form 4 parsing."""

from datetime import date, timedelta

import pandas as pd
import pytest

from edgar_client.insider_parser import parse_insider_transactions, score_insider_buying


class TestParseInsiderTransactions:
    """Tests for Form 4 transaction filtering."""

    def test_filters_to_purchases_only(self, sample_form4_transactions):
        """Should only return transactions with type 'P'."""
        result = parse_insider_transactions(sample_form4_transactions)

        assert len(result) == 3  # 3 purchases, 1 sale
        assert all(result["transaction_type"] == "P")

    def test_adds_dollar_value(self, sample_form4_transactions):
        """Should compute dollar_value = shares * price."""
        result = parse_insider_transactions(sample_form4_transactions)

        # First purchase: 10000 shares * $15.50
        assert result.iloc[0]["dollar_value"] == 10000 * 15.50

    def test_handles_empty_dataframe(self):
        """Should handle empty DataFrame."""
        result = parse_insider_transactions(pd.DataFrame())
        assert result.empty

    def test_handles_no_purchases(self):
        """Should return empty when only sales exist."""
        df = pd.DataFrame({
            "filed_date": [date.today()],
            "transaction_date": [date.today()],
            "insider_name": ["Seller"],
            "insider_title": ["Director"],
            "transaction_type": ["S"],
            "shares": [-1000],
            "price": [20.0],
            "shares_after": [0],
            "is_direct": [True],
        })

        result = parse_insider_transactions(df)
        assert result.empty


class TestScoreInsiderBuying:
    """Tests for insider buying score computation."""

    def test_scores_purchases(self, sample_form4_transactions):
        """Should produce a positive score when purchases exist."""
        cutoff = date.today() - timedelta(days=90)
        score = score_insider_buying(sample_form4_transactions, cutoff)

        assert score > 0

    def test_zero_for_no_purchases(self):
        """Should return 0 when no purchases exist."""
        df = pd.DataFrame({
            "filed_date": [date.today()],
            "transaction_date": [date.today()],
            "insider_name": ["Seller"],
            "insider_title": ["Director"],
            "transaction_type": ["S"],
            "shares": [-1000],
            "price": [20.0],
            "shares_after": [0],
            "is_direct": [True],
        })

        score = score_insider_buying(df, date.today() - timedelta(days=90))
        assert score == 0.0

    def test_zero_for_empty(self):
        """Should return 0 for empty DataFrame."""
        score = score_insider_buying(pd.DataFrame(), date.today())
        assert score == 0.0

    def test_higher_score_for_more_insiders(self):
        """More unique insiders buying should yield higher score."""
        cutoff = date.today() - timedelta(days=90)

        # One insider
        df1 = pd.DataFrame({
            "filed_date": [date.today() - timedelta(days=5)],
            "transaction_date": [date.today() - timedelta(days=5)],
            "insider_name": ["John CEO"],
            "insider_title": ["Chief Executive Officer"],
            "transaction_type": ["P"],
            "shares": [10000],
            "price": [15.0],
            "shares_after": [50000],
            "is_direct": [True],
        })

        # Three insiders
        df3 = pd.DataFrame({
            "filed_date": [date.today() - timedelta(days=5)] * 3,
            "transaction_date": [date.today() - timedelta(days=5)] * 3,
            "insider_name": ["John CEO", "Jane CFO", "Bob Director"],
            "insider_title": ["CEO", "CFO", "Director"],
            "transaction_type": ["P", "P", "P"],
            "shares": [10000, 5000, 2000],
            "price": [15.0, 15.0, 15.0],
            "shares_after": [50000, 25000, 12000],
            "is_direct": [True, True, True],
        })

        score1 = score_insider_buying(df1, cutoff)
        score3 = score_insider_buying(df3, cutoff)

        assert score3 > score1

    def test_executive_weighting(self):
        """CEO/CFO purchases should be weighted more than director purchases."""
        cutoff = date.today() - timedelta(days=90)

        # CEO purchase
        ceo_df = pd.DataFrame({
            "filed_date": [date.today() - timedelta(days=5)],
            "transaction_date": [date.today() - timedelta(days=5)],
            "insider_name": ["John"],
            "insider_title": ["Chief Executive Officer"],
            "transaction_type": ["P"],
            "shares": [10000],
            "price": [15.0],
            "shares_after": [50000],
            "is_direct": [True],
        })

        # Director purchase (same amount)
        dir_df = pd.DataFrame({
            "filed_date": [date.today() - timedelta(days=5)],
            "transaction_date": [date.today() - timedelta(days=5)],
            "insider_name": ["Bob"],
            "insider_title": ["Director"],
            "transaction_type": ["P"],
            "shares": [10000],
            "price": [15.0],
            "shares_after": [50000],
            "is_direct": [True],
        })

        ceo_score = score_insider_buying(ceo_df, cutoff)
        dir_score = score_insider_buying(dir_df, cutoff)

        assert ceo_score > dir_score

    def test_filters_by_cutoff_date(self, sample_form4_transactions):
        """Should only count transactions after cutoff date."""
        # Set cutoff to only include very recent transactions
        cutoff = date.today() - timedelta(days=8)
        score_recent = score_insider_buying(sample_form4_transactions, cutoff)

        cutoff_old = date.today() - timedelta(days=90)
        score_all = score_insider_buying(sample_form4_transactions, cutoff_old)

        # More transactions in wider window → higher score
        assert score_all >= score_recent
