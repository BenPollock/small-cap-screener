"""Output rendering: terminal (Rich), CSV, and markdown formats.

Every screen run also saves to data/screens/ as JSON for machine-readability.
"""

import json
import logging
import sys
from datetime import date, datetime
from pathlib import Path

import pandas as pd
from rich.console import Console
from rich.table import Table

logger = logging.getLogger(__name__)

# Columns to display in output
DISPLAY_COLUMNS = [
    ("rank", "Rank"),
    ("ticker", "Ticker"),
    ("sector", "Sector"),
    ("market_cap", "MktCap"),
    ("roc_6m", "6mROC"),
    ("quality_rank", "Quality"),
    ("insider_rank", "Insider"),
    ("composite_score", "Composite"),
]


def render_output(df: pd.DataFrame, output_format: str = "terminal") -> None:
    """Render the ranked results in the specified format.

    Args:
        df: Ranked DataFrame from scorer.
        output_format: One of 'terminal', 'csv', 'markdown'.
    """
    if df.empty:
        logger.warning("No results to display.")
        return

    display_df = _prepare_display_df(df)

    if output_format == "terminal":
        _render_terminal(display_df)
    elif output_format == "csv":
        _render_csv(display_df)
    elif output_format == "markdown":
        _render_markdown(display_df)
    else:
        logger.error("Unknown output format: %s", output_format)


def save_screen(df: pd.DataFrame, screens_dir: str = "./data/screens") -> Path:
    """Save screen results as timestamped JSON.

    Always called after every screen run, regardless of output format.
    Returns the path to the saved file.
    """
    screens_path = Path(screens_dir)
    screens_path.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"screen_{timestamp}.json"
    filepath = screens_path / filename

    # Convert to JSON-serializable format
    records = df.to_dict(orient="records")
    for record in records:
        for key, val in record.items():
            if isinstance(val, (date, datetime)):
                record[key] = val.isoformat()
            elif isinstance(val, float) and pd.isna(val):
                record[key] = None

    output = {
        "timestamp": datetime.now().isoformat(),
        "count": len(records),
        "results": records,
    }

    with open(filepath, "w") as f:
        json.dump(output, f, indent=2, default=str)

    logger.info("Screen saved to %s", filepath)
    return filepath


def _prepare_display_df(df: pd.DataFrame) -> pd.DataFrame:
    """Prepare a display-ready DataFrame with formatted columns."""
    display = pd.DataFrame()

    for col, label in DISPLAY_COLUMNS:
        if col in df.columns:
            if col == "market_cap":
                display[label] = df[col].apply(_format_market_cap)
            elif col in ("roc_6m", "quality_rank", "insider_rank", "composite_score"):
                display[label] = df[col].apply(lambda x: f"{x:.1f}" if pd.notna(x) else "N/A")
            elif col == "rank":
                display[label] = df[col].astype(int)
            else:
                display[label] = df[col].fillna("N/A")
        else:
            display[label] = "N/A"

    return display


def _format_market_cap(val) -> str:
    """Format market cap: $1.5B, $500M, etc."""
    if pd.isna(val):
        return "N/A"
    if val >= 1_000_000_000:
        return f"${val / 1_000_000_000:.1f}B"
    return f"${val / 1_000_000:.0f}M"


def _render_terminal(df: pd.DataFrame) -> None:
    """Render as Rich terminal table."""
    console = Console()
    table = Table(title=f"Small-Cap Screener Results — {date.today()}", show_lines=False)

    for col in df.columns:
        justify = "right" if col not in ("Ticker", "Sector") else "left"
        table.add_column(col, justify=justify)

    for _, row in df.iterrows():
        table.add_row(*[str(v) for v in row])

    console.print(table)


def _render_csv(df: pd.DataFrame) -> None:
    """Render as CSV to stdout."""
    df.to_csv(sys.stdout, index=False)


def _render_markdown(df: pd.DataFrame) -> None:
    """Render as markdown table and save to data/screens/."""
    screens_dir = Path("./data/screens")
    screens_dir.mkdir(parents=True, exist_ok=True)

    filepath = screens_dir / f"screen_{date.today().isoformat()}.md"

    lines = [f"# Small-Cap Screen — {date.today()}\n"]
    lines.append("| " + " | ".join(df.columns) + " |")
    lines.append("| " + " | ".join(["---"] * len(df.columns)) + " |")
    for _, row in df.iterrows():
        lines.append("| " + " | ".join(str(v) for v in row) + " |")

    md_content = "\n".join(lines) + "\n"

    with open(filepath, "w") as f:
        f.write(md_content)

    # Also print to stdout
    print(md_content)
    logger.info("Markdown saved to %s", filepath)
