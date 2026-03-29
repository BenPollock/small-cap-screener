"""E2E tests for CLI commands using Click's CliRunner."""

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from click.testing import CliRunner

from src.cli import cli


@pytest.fixture
def runner():
    return CliRunner()


class TestScreenerRun:
    """Tests for the 'screener run' command."""

    @patch("src.pipeline.run_pipeline")
    def test_default_run(self, mock_pipeline, runner):
        """Should invoke pipeline with default options."""
        mock_pipeline.return_value = pd.DataFrame()

        result = runner.invoke(cli, ["run"])

        assert result.exit_code == 0
        mock_pipeline.assert_called_once_with(
            top=30,
            output_format="terminal",
            skip_edgar=False,
            min_mcap=200,
            max_mcap=2000,
            cache_dir="./data/cache",
        )

    @patch("src.pipeline.run_pipeline")
    def test_custom_flags(self, mock_pipeline, runner):
        """Should pass custom flags through to pipeline."""
        mock_pipeline.return_value = pd.DataFrame()

        result = runner.invoke(cli, [
            "run",
            "--top", "10",
            "--output", "csv",
            "--skip-edgar",
            "--min-mcap", "300",
            "--max-mcap", "1500",
        ])

        assert result.exit_code == 0
        mock_pipeline.assert_called_once_with(
            top=10,
            output_format="csv",
            skip_edgar=True,
            min_mcap=300,
            max_mcap=1500,
            cache_dir="./data/cache",
        )

    def test_invalid_output_format(self, runner):
        """Should reject invalid output format."""
        result = runner.invoke(cli, ["run", "--output", "invalid"])
        assert result.exit_code != 0
        assert "Invalid value" in result.output


class TestPortfolioCommands:
    """Tests for portfolio CLI commands."""

    @patch("src.portfolio.log_portfolio")
    def test_portfolio_log(self, mock_log, runner):
        """Should invoke portfolio log."""
        result = runner.invoke(cli, ["portfolio", "log"])
        assert result.exit_code == 0
        mock_log.assert_called_once()

    @patch("src.portfolio.log_portfolio")
    def test_portfolio_log_custom_top(self, mock_log, runner):
        """Should pass --top flag to portfolio log."""
        result = runner.invoke(cli, ["portfolio", "log", "--top", "5"])
        assert result.exit_code == 0
        mock_log.assert_called_once_with(top=5, cache_dir="./data/cache")

    @patch("src.portfolio.show_portfolio")
    def test_portfolio_show(self, mock_show, runner):
        """Should invoke portfolio show."""
        result = runner.invoke(cli, ["portfolio", "show"])
        assert result.exit_code == 0
        mock_show.assert_called_once()


class TestValidateCommand:
    """Tests for the validate command."""

    @patch("src.validate.run_validation")
    def test_validate_default(self, mock_validate, runner):
        """Should invoke validation with default period."""
        mock_validate.return_value = pd.DataFrame()

        result = runner.invoke(cli, ["validate"])
        assert result.exit_code == 0
        mock_validate.assert_called_once_with(period="10y")

    @patch("src.validate.run_validation")
    def test_validate_custom_period(self, mock_validate, runner):
        """Should pass custom period."""
        mock_validate.return_value = pd.DataFrame()

        result = runner.invoke(cli, ["validate", "--period", "5y"])
        assert result.exit_code == 0
        mock_validate.assert_called_once_with(period="5y")


class TestHelpMessages:
    """Tests that help output is useful."""

    def test_main_help(self, runner):
        """Should show help without error."""
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "Small-cap stock screener" in result.output

    def test_run_help(self, runner):
        """Should show run command help."""
        result = runner.invoke(cli, ["run", "--help"])
        assert result.exit_code == 0
        assert "--top" in result.output
        assert "--skip-edgar" in result.output

    def test_portfolio_help(self, runner):
        """Should show portfolio help."""
        result = runner.invoke(cli, ["portfolio", "--help"])
        assert result.exit_code == 0
        assert "log" in result.output
        assert "show" in result.output
