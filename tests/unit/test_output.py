"""Unit tests for output rendering (terminal, CSV, markdown)."""

import json
from datetime import date
from io import StringIO
from pathlib import Path

import pandas as pd
import pytest

from src.output import _prepare_display_df, render_output, save_screen


@pytest.fixture
def simple_ranked_df():
    """Minimal ranked DataFrame for output tests."""
    return pd.DataFrame({
        "rank": [1, 2, 3],
        "ticker": ["ACME", "BETA", "GAMA"],
        "sector": ["Technology", "Healthcare", "Industrials"],
        "market_cap": [500_000_000, 800_000_000, 1_200_000_000],
        "roc_6m": [25.0, 15.0, -5.0],
        "quality_rank": [80.0, 60.0, 40.0],
        "insider_rank": [70.0, 0.0, 30.0],
        "composite_score": [72.0, 48.0, 28.0],
    })


class TestPrepareDisplayDf:
    """Tests for display DataFrame formatting."""

    def test_formats_market_cap(self, simple_ranked_df):
        """Should format market cap as $500M, $1.2B, etc."""
        result = _prepare_display_df(simple_ranked_df)

        assert result.iloc[0]["MktCap"] == "$500M"
        assert result.iloc[2]["MktCap"] == "$1.2B"

    def test_formats_percentages(self, simple_ranked_df):
        """Should format scores with one decimal."""
        result = _prepare_display_df(simple_ranked_df)

        assert result.iloc[0]["6mROC"] == "25.0"
        assert result.iloc[0]["Composite"] == "72.0"


class TestRenderOutput:
    """Tests for output rendering in different formats."""

    def test_terminal_output(self, simple_ranked_df, capsys):
        """Should render terminal table without errors."""
        render_output(simple_ranked_df, output_format="terminal")
        captured = capsys.readouterr()
        assert "ACME" in captured.out

    def test_csv_output(self, simple_ranked_df, capsys):
        """Should render valid CSV to stdout."""
        render_output(simple_ranked_df, output_format="csv")
        captured = capsys.readouterr()

        # Parse CSV output
        df = pd.read_csv(StringIO(captured.out))
        assert len(df) == 3
        assert "Ticker" in df.columns

    def test_markdown_output(self, simple_ranked_df, tmp_path, monkeypatch, capsys):
        """Should render markdown and save to file."""
        screens_dir = tmp_path / "screens"
        screens_dir.mkdir()
        monkeypatch.chdir(tmp_path)

        # Create data/screens relative to tmp_path
        (tmp_path / "data" / "screens").mkdir(parents=True)

        render_output(simple_ranked_df, output_format="markdown")
        captured = capsys.readouterr()
        assert "|" in captured.out  # Markdown table syntax
        assert "ACME" in captured.out

    def test_empty_df(self, capsys):
        """Should handle empty DataFrame gracefully."""
        render_output(pd.DataFrame(), output_format="terminal")
        # Should not crash


class TestSaveScreen:
    """Tests for screen JSON saving."""

    def test_saves_json(self, simple_ranked_df, tmp_path):
        """Should save screen results as JSON."""
        screens_dir = tmp_path / "screens"
        filepath = save_screen(simple_ranked_df, screens_dir=str(screens_dir))

        assert filepath.exists()
        with open(filepath) as f:
            data = json.load(f)

        assert data["count"] == 3
        assert len(data["results"]) == 3
        assert data["results"][0]["ticker"] == "ACME"

    def test_creates_directory(self, simple_ranked_df, tmp_path):
        """Should create screens directory if it doesn't exist."""
        screens_dir = tmp_path / "new_dir" / "screens"
        filepath = save_screen(simple_ranked_df, screens_dir=str(screens_dir))

        assert filepath.exists()
        assert screens_dir.exists()
