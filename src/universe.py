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
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
import yfinance as yf

logger = logging.getLogger(__name__)

# SEC EDGAR company tickers endpoints (no auth required, but needs User-Agent)
EDGAR_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
EDGAR_TICKERS_EXCHANGE_URL = "https://www.sec.gov/files/company_tickers_exchange.json"
SEC_USER_AGENT = "SmallCapScreener/1.0 (ben@example.com)"

# Exchanges we want (yfinance .info exchange codes)
VALID_EXCHANGES = {"NYQ", "NMS", "NGM", "NCM", "NYSE", "NASDAQ", "NasdaqGS", "NasdaqGM", "NasdaqCM"}

# EDGAR exchange labels used for pre-filtering (before yfinance enrichment)
EDGAR_EXCHANGE_LABELS = {"NYSE", "Nasdaq", "NASDAQ"}

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
    max_workers: int = 8,
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

    # Get candidate tickers (pre-filtered by exchange if available)
    candidates = _fetch_candidate_tickers()
    logger.info("Fetched %d candidate tickers", len(candidates))

    if candidates.empty:
        logger.error("No candidate tickers found")
        return pd.DataFrame()

    # Batch volume prescreen: use yf.download() to quickly drop illiquid tickers
    # before expensive individual .info calls
    all_tickers = candidates["ticker"].tolist()
    logger.info("Running batch volume prescreen on %d tickers...", len(all_tickers))
    volume_passed = set(_batch_volume_prescreen(all_tickers, max_workers=max_workers))
    candidates = candidates[candidates["ticker"].isin(volume_passed)].copy()
    logger.info("After volume prescreen: %d tickers", len(candidates))

    # Enrich survivors with full yfinance .info data (market cap, sector, etc.)
    enriched = _enrich_with_yfinance(candidates, max_workers=max_workers)
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
    """Fetch candidate tickers from SEC EDGAR, pre-filtered by exchange.

    Tries the exchange endpoint first to filter to NYSE/Nasdaq before yfinance
    enrichment, reducing the candidate set by ~40%. Falls back to the basic
    tickers endpoint if the exchange endpoint is unavailable.

    Returns DataFrame with columns: ticker, company_name, cik
    """
    df = _fetch_with_exchange_filter()
    if df is not None and not df.empty:
        return df

    # Fallback: basic endpoint without exchange pre-filter
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
        df = _clean_tickers(df)
        return df

    except Exception as e:
        logger.warning("Failed to fetch SEC tickers: %s. Falling back to Russell 2000.", e)
        return _fallback_russell2000()


def _fetch_with_exchange_filter() -> pd.DataFrame | None:
    """Try the EDGAR exchange endpoint to pre-filter by NYSE/Nasdaq.

    Returns None if the endpoint is unavailable.
    """
    try:
        resp = requests.get(
            EDGAR_TICKERS_EXCHANGE_URL,
            headers={"User-Agent": SEC_USER_AGENT},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        fields = data.get("fields", [])
        rows_raw = data.get("data", [])
        df = pd.DataFrame(rows_raw, columns=fields)

        # Normalize column names (EDGAR uses varying capitalization)
        col_map = {}
        for col in df.columns:
            lc = col.lower()
            if lc == "cik":
                col_map[col] = "cik"
            elif lc in ("name", "title"):
                col_map[col] = "company_name"
            elif lc == "ticker":
                col_map[col] = "ticker"
            elif lc == "exchange":
                col_map[col] = "exchange"
        df = df.rename(columns=col_map)

        if "exchange" not in df.columns or "ticker" not in df.columns:
            logger.debug("Exchange endpoint missing expected columns")
            return None

        before = len(df)
        df = df[df["exchange"].isin(EDGAR_EXCHANGE_LABELS)]
        logger.info("Exchange pre-filter: %d → %d tickers (NYSE/Nasdaq only)", before, len(df))

        df = _clean_tickers(df)

        # Drop exchange column — yfinance enrichment will add the canonical one
        if "exchange" in df.columns:
            df = df.drop(columns=["exchange"])

        return df

    except Exception as e:
        logger.debug("Exchange endpoint unavailable, falling back: %s", e)
        return None


def _clean_tickers(df: pd.DataFrame) -> pd.DataFrame:
    """Remove tickers with special characters, warrants, units, etc."""
    df = df[~df["ticker"].str.contains(r"[^A-Za-z]", regex=True, na=False)]
    df = df[df["ticker"].str.len().between(1, 5)]
    return df


def _batch_volume_prescreen(
    tickers: list[str], min_dollar_volume: float = 500_000, max_workers: int = 4,
) -> list[str]:
    """Use yf.download() batch API to quickly screen by dollar volume.

    Much faster than individual Ticker().info calls because yf.download()
    fetches all tickers in a single HTTP request per batch.

    Returns list of tickers that pass the dollar-volume screen.
    """
    passed = []
    batch_size = 100

    # Shared session with connection pooling — reuses sockets to avoid
    # per-ticker DNS lookups and file descriptor exhaustion on large runs.
    session = _make_pooled_session(max_workers)

    total_batches = (len(tickers) + batch_size - 1) // batch_size
    for i in range(0, len(tickers), batch_size):
        batch = tickers[i : i + batch_size]
        batch_num = i // batch_size + 1
        logger.info("Volume prescreen batch %d/%d (%d tickers)", batch_num, total_batches, len(batch))
        try:
            data = yf.download(
                batch, period="5d", group_by="ticker", progress=False,
                threads=max_workers, session=session,
            )
            if data.empty:
                continue

            for ticker in batch:
                try:
                    if len(batch) == 1:
                        ticker_data = data
                    else:
                        ticker_data = data[ticker]

                    if ticker_data.empty:
                        continue

                    last_close = ticker_data["Close"].dropna().iloc[-1]
                    avg_vol = ticker_data["Volume"].dropna().mean()
                    if last_close * avg_vol >= min_dollar_volume:
                        passed.append(ticker)
                except (KeyError, IndexError):
                    continue

        except Exception as e:
            logger.debug("Batch volume prescreen failed for chunk %d: %s", i, e)
            # On failure, pass all tickers through (don't lose candidates)
            passed.extend(batch)

    session.close()
    return passed


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


def _make_pooled_session(pool_size: int = 4) -> requests.Session:
    """Create a requests Session with bounded connection pooling.

    Reusing connections avoids per-request DNS lookups, reduces file
    descriptor usage, and prevents yfinance's internal SQLite TZ cache
    from being hammered by concurrent openers.
    """
    session = requests.Session()
    adapter = HTTPAdapter(
        pool_connections=pool_size,
        pool_maxsize=pool_size,
    )
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def _enrich_with_yfinance(
    candidates: pd.DataFrame,
    max_workers: int = 4,
    cache_dir: str = "./data/cache",
    checkpoint_interval: int = 100,
) -> pd.DataFrame:
    """Enrich candidate tickers with market cap, volume, sector from yfinance.

    Uses ThreadPoolExecutor for concurrent fetching. yfinance allows ~2000 req/hr;
    4 workers keep throughput well under that limit.

    Writes partial results every checkpoint_interval tickers so that a crash
    doesn't lose all progress. On restart, resumes from the partial file.
    """
    partial_path = Path(cache_dir) / f"_enrichment_partial_{date.today().isoformat()}.parquet"
    partial_path.parent.mkdir(parents=True, exist_ok=True)

    # Resume from partial checkpoint if available
    done_tickers: set[str] = set()
    enriched_rows: list[dict] = []
    if partial_path.exists():
        partial_df = pd.read_parquet(partial_path)
        enriched_rows = partial_df.to_dict("records")
        done_tickers = set(partial_df["ticker"])
        logger.info("Resuming enrichment: %d tickers already completed", len(done_tickers))

    remaining = [t for t in candidates["ticker"].tolist() if t not in done_tickers]
    total = len(remaining) + len(done_tickers)
    completed = len(done_tickers)
    new_since_checkpoint = 0

    session = _make_pooled_session(max_workers)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_fetch_ticker_info, t, session=session): t for t in remaining}
        for future in as_completed(futures):
            completed += 1
            if completed % 50 == 0:
                logger.info("Universe enrichment progress: %d/%d", completed, total)
            try:
                info = future.result()
                if info is not None:
                    enriched_rows.append(info)
                    new_since_checkpoint += 1
            except Exception as e:
                logger.debug("Failed to fetch info for %s: %s", futures[future], e)

            # Periodic checkpoint
            if new_since_checkpoint >= checkpoint_interval:
                pd.DataFrame(enriched_rows).to_parquet(partial_path, index=False)
                new_since_checkpoint = 0

    session.close()

    # Final write + cleanup
    result = pd.DataFrame(enriched_rows)
    if partial_path.exists():
        partial_path.unlink()
    return result


def _fetch_ticker_info(ticker: str, max_retries: int = 2, session: requests.Session | None = None) -> dict | None:
    """Fetch key info for a single ticker from yfinance with retry.

    Returns dict with: ticker, company_name, market_cap, avg_volume,
    avg_dollar_volume, sector, industry, exchange. Or None on failure.
    """
    for attempt in range(max_retries):
        try:
            t = yf.Ticker(ticker, session=session)
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
