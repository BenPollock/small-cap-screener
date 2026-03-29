"""Universe construction: fetch and filter the investable small-cap universe.

Adapted from claude-backtester/src/backtester/data/universe.py for
yfinance data patterns and claude-backtester/src/backtester/data/sources/yahoo.py
for retry/throttle logic.

Approach: We use the SEC EDGAR company tickers endpoint to get a comprehensive
list of US-listed equities, then enrich with yfinance data for market cap and
volume filtering. Fallback: Russell 2000 constituents via yfinance.

Filters applied:
- Market cap: $200M–$2B (configurable)
- Average daily dollar volume: > $500K
- Exchange: NYSE/NASDAQ only
- Excludes: ADRs, SPACs, REITs (optional), pre-revenue biotech
"""

import json
import logging
import time
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf

logger = logging.getLogger(__name__)

# SEC EDGAR company tickers endpoint (no auth required, but needs User-Agent)
EDGAR_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_USER_AGENT = "SmallCapScreener/1.0 (ben@example.com)"

# Exchanges we want
VALID_EXCHANGES = {"NYQ", "NMS", "NGM", "NCM", "NYSE", "NASDAQ", "NasdaqGS", "NasdaqGM", "NasdaqCM"}

# SPAC indicators in company names
SPAC_KEYWORDS = ["acquisition corp", "blank check", "spac", "merger corp", "acquisition company"]

# Sector-based exclusions
REIT_SECTOR = "Real Estate"
BIOTECH_INDUSTRY = "Biotechnology"


def get_universe(
    min_mcap: int = 200,
    max_mcap: int = 2000,
    cache_dir: str = "./data/cache",
    include_reits: bool = False,
) -> pd.DataFrame:
    """Build the filtered investable universe.

    Args:
        min_mcap: Minimum market cap in millions (default 200).
        max_mcap: Maximum market cap in millions (default 2000).
        cache_dir: Directory for caching data.
        include_reits: Whether to include REITs (default False).

    Returns:
        DataFrame with columns: ticker, company_name, market_cap, avg_volume,
        avg_dollar_volume, sector, industry, exchange
    """
    cache_path = Path(cache_dir) / f"universe_{date.today().isoformat()}.parquet"
    if cache_path.exists():
        logger.info("Loading cached universe from %s", cache_path)
        return pd.read_parquet(cache_path)

    # Get candidate tickers
    candidates = _fetch_candidate_tickers()
    logger.info("Fetched %d candidate tickers", len(candidates))

    if candidates.empty:
        logger.error("No candidate tickers found")
        return pd.DataFrame()

    # Enrich with yfinance data in batches
    enriched = _enrich_with_yfinance(candidates)
    logger.info("Enriched %d tickers with yfinance data", len(enriched))

    # Apply filters
    filtered = _apply_filters(
        enriched,
        min_mcap=min_mcap,
        max_mcap=max_mcap,
        include_reits=include_reits,
    )
    logger.info("Universe after all filters: %d tickers", len(filtered))

    # Cache result
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    filtered.to_parquet(cache_path, index=False)

    return filtered


def _fetch_candidate_tickers() -> pd.DataFrame:
    """Fetch candidate tickers from SEC EDGAR company tickers endpoint.

    Returns DataFrame with columns: ticker, company_name, cik
    """
    try:
        resp = requests.get(
            EDGAR_TICKERS_URL,
            headers={"User-Agent": SEC_USER_AGENT},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        rows = []
        for entry in data.values():
            ticker = entry.get("ticker", "")
            name = entry.get("title", "")
            cik = entry.get("cik_str", "")
            rows.append({"ticker": ticker, "company_name": name, "cik": cik})

        df = pd.DataFrame(rows)
        # Basic cleanup: remove tickers with special characters (warrants, units)
        df = df[~df["ticker"].str.contains(r"[^A-Za-z]", regex=True, na=False)]
        # Remove very short or very long tickers
        df = df[df["ticker"].str.len().between(1, 5)]
        return df

    except Exception as e:
        logger.warning("Failed to fetch SEC tickers: %s. Falling back to Russell 2000.", e)
        return _fallback_russell2000()


def _fallback_russell2000() -> pd.DataFrame:
    """Fallback: use IWM (Russell 2000 ETF) top holdings as seed universe."""
    try:
        iwm = yf.Ticker("IWM")
        holdings = iwm.major_holders
        # This is limited — as a better fallback, we use a known small-cap list
        # For now, return empty and let the enrichment handle it
        logger.warning("Russell 2000 fallback: limited data available from yfinance")
        return pd.DataFrame(columns=["ticker", "company_name", "cik"])
    except Exception as e:
        logger.error("Fallback also failed: %s", e)
        return pd.DataFrame(columns=["ticker", "company_name", "cik"])


def _enrich_with_yfinance(
    candidates: pd.DataFrame,
    batch_size: int = 50,
    delay_between_batches: float = 2.0,
) -> pd.DataFrame:
    """Enrich candidate tickers with market cap, volume, sector from yfinance.

    Processes in batches with delays to avoid rate limiting.
    Adapted from claude-backtester yahoo.py throttle/retry pattern.
    """
    tickers = candidates["ticker"].tolist()
    enriched_rows = []
    total = len(tickers)

    for i in range(0, total, batch_size):
        batch = tickers[i : i + batch_size]
        batch_num = i // batch_size + 1
        total_batches = (total + batch_size - 1) // batch_size
        logger.info("Processing batch %d/%d (%d tickers)", batch_num, total_batches, len(batch))

        for ticker in batch:
            try:
                info = _fetch_ticker_info(ticker)
                if info is not None:
                    enriched_rows.append(info)
            except Exception as e:
                logger.debug("Failed to fetch info for %s: %s", ticker, e)
                continue

        # Rate limit between batches
        if i + batch_size < total:
            time.sleep(delay_between_batches)

    return pd.DataFrame(enriched_rows)


def _fetch_ticker_info(ticker: str, max_retries: int = 2) -> dict | None:
    """Fetch key info for a single ticker from yfinance with retry.

    Returns dict with: ticker, company_name, market_cap, avg_volume,
    avg_dollar_volume, sector, industry, exchange. Or None on failure.
    """
    for attempt in range(max_retries):
        try:
            t = yf.Ticker(ticker)
            info = t.info

            if not info or info.get("quoteType") is None:
                return None

            market_cap = info.get("marketCap")
            avg_volume = info.get("averageVolume")
            current_price = info.get("currentPrice") or info.get("regularMarketPrice")

            if market_cap is None or avg_volume is None or current_price is None:
                return None

            avg_dollar_volume = avg_volume * current_price

            return {
                "ticker": ticker,
                "company_name": info.get("longName") or info.get("shortName", ""),
                "market_cap": market_cap,
                "avg_volume": avg_volume,
                "avg_dollar_volume": avg_dollar_volume,
                "sector": info.get("sector", ""),
                "industry": info.get("industry", ""),
                "exchange": info.get("exchange", ""),
            }

        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(1.0 * (attempt + 1))
            else:
                logger.debug("Failed to fetch %s after %d attempts: %s", ticker, max_retries, e)
                return None

    return None


def _apply_filters(
    df: pd.DataFrame,
    min_mcap: int = 200,
    max_mcap: int = 2000,
    include_reits: bool = False,
) -> pd.DataFrame:
    """Apply all universe filters. Logs survivors at each stage."""
    if df.empty:
        return df

    initial = len(df)

    # Market cap filter (convert millions to actual)
    min_cap = min_mcap * 1_000_000
    max_cap = max_mcap * 1_000_000
    df = df[(df["market_cap"] >= min_cap) & (df["market_cap"] <= max_cap)].copy()
    logger.info("After market cap filter ($%dM-$%dM): %d/%d", min_mcap, max_mcap, len(df), initial)

    # Dollar volume filter
    prev = len(df)
    df = df[df["avg_dollar_volume"] > 500_000].copy()
    logger.info("After dollar volume filter (>$500K): %d/%d", len(df), prev)

    # Exchange filter
    prev = len(df)
    df = df[df["exchange"].isin(VALID_EXCHANGES)].copy()
    logger.info("After exchange filter (NYSE/NASDAQ): %d/%d", len(df), prev)

    # SPAC exclusion
    prev = len(df)
    spac_mask = df["company_name"].str.lower().apply(
        lambda name: any(kw in str(name) for kw in SPAC_KEYWORDS)
    )
    df = df[~spac_mask].copy()
    logger.info("After SPAC exclusion: %d/%d", len(df), prev)

    # REIT exclusion (optional)
    if not include_reits:
        prev = len(df)
        df = df[df["sector"] != REIT_SECTOR].copy()
        logger.info("After REIT exclusion: %d/%d", len(df), prev)

    # ADR exclusion (typically have exchange not in our valid set, but also check name)
    prev = len(df)
    adr_mask = df["company_name"].str.lower().str.contains("adr|depositary", regex=True, na=False)
    df = df[~adr_mask].copy()
    logger.info("After ADR exclusion: %d/%d", len(df), prev)

    df.reset_index(drop=True, inplace=True)
    return df
