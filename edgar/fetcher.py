"""EDGAR API client for fetching Form 4 insider filings.

Adapted from claude-backtester/src/backtester/data/edgar_insider.py and
edgar_source.py. Uses the edgartools library for structured access to
SEC EDGAR filings.

Key behaviors:
- Rate limited to 10 req/sec via RateLimiter
- Exponential backoff on 403/429 errors
- User-Agent header required by SEC
"""

import logging
from datetime import date

import pandas as pd

from edgar.rate_limiter import RateLimiter, edgar_retry

logger = logging.getLogger(__name__)

try:
    from edgartools import Company, set_identity
except ImportError:
    try:
        from edgar import Company, set_identity
    except ImportError:
        Company = None  # type: ignore[assignment,misc]
        set_identity = None  # type: ignore[assignment]

# Default User-Agent for SEC — must include contact email
DEFAULT_USER_AGENT = "SmallCapScreener/1.0 (ben@example.com)"

# Only open-market purchases (P) and sales (S)
_TRANSACTION_CODES = {"P", "S"}

_FORM4_COLUMNS = [
    "filed_date",
    "transaction_date",
    "insider_name",
    "insider_title",
    "transaction_type",
    "shares",
    "price",
    "shares_after",
    "is_direct",
]

# Column name candidates — edgartools versions expose different names
_COL_CANDIDATES = {
    "transaction_code": ["transaction_code", "code", "Code", "TransactionCode"],
    "shares": ["transaction_shares", "shares", "Shares", "TransactionShares", "Amount"],
    "price": ["transaction_price_per_share", "price", "Price", "PricePerShare"],
    "transaction_date": ["transaction_date", "Date", "TransactionDate"],
    "acquired_disposed": ["acquired_disposed_code", "AcquiredDisposedCode", "acquired_disposed"],
    "shares_after": ["shares_owned_following", "Remaining Shares", "SharesOwnedFollowingTransaction"],
    "direct_indirect": ["direct_or_indirect_ownership", "DirectOrIndirectOwnership", "ownership_nature"],
    "insider_name": ["Insider", "insider_name", "owner_name"],
    "insider_title": ["Position", "insider_title", "owner_title"],
}


def _resolve_col(df: pd.DataFrame, key: str) -> str | None:
    """Return the first matching column name from df for logical key."""
    for candidate in _COL_CANDIDATES.get(key, []):
        if candidate in df.columns:
            return candidate
    return None


class EdgarClient:
    """EDGAR API client for Form 4 insider trading data.

    Adapted from claude-backtester/src/backtester/data/edgar_insider.py.
    """

    def __init__(
        self,
        user_agent: str = DEFAULT_USER_AGENT,
        max_filings: int = 50,
    ):
        if Company is None:
            raise ImportError(
                "edgartools is required. Install with: pip install edgartools"
            )
        self.user_agent = user_agent
        self.max_filings = max_filings
        self._rate_limiter = RateLimiter()

        if set_identity is not None:
            set_identity(user_agent)

    @edgar_retry()
    def fetch_form4(self, symbol: str) -> pd.DataFrame:
        """Fetch Form 4 insider transactions for a symbol.

        Returns DataFrame with columns: filed_date, transaction_date,
        insider_name, insider_title, transaction_type (P/S), shares,
        price, shares_after, is_direct

        Adapted from claude-backtester EdgarInsiderSource.fetch().
        """
        self._rate_limiter.wait()
        company = Company(symbol)

        try:
            filings = company.get_filings(form="4")
        except Exception as exc:
            from edgar.rate_limiter import is_rate_limit_error
            if is_rate_limit_error(exc):
                raise
            logger.warning("Could not retrieve Form 4 filings for %s", symbol)
            return self._empty_df()

        if filings is None:
            return self._empty_df()

        # Verify issuer CIK matches target (Bug 1 fix from backtester)
        target_cik = getattr(company, "cik", None)

        rows: list[dict] = []
        for filing in filings[: self.max_filings]:
            try:
                self._rate_limiter.wait()
                parsed = filing.obj()
                if parsed is None:
                    continue

                # Bug 1 fix: verify issuer matches target company
                issuer = getattr(parsed, "issuer", None)
                if issuer is not None and target_cik is not None:
                    issuer_cik = getattr(issuer, "cik", None)
                    if issuer_cik is not None and str(issuer_cik).lstrip("0") != str(target_cik).lstrip("0"):
                        continue

                filed_date = self._parse_date(
                    getattr(filing, "filing_date", None)
                    or getattr(filing, "filed", None)
                )

                owner_name = getattr(parsed, "owner_name", "") or ""
                owner_title = getattr(parsed, "owner_title", "") or ""

                # Try multiple edgartools APIs (Bug 2 fix from backtester)
                txn_df = getattr(parsed, "non_derivative_table", None)
                if txn_df is not None and not isinstance(txn_df, pd.DataFrame):
                    if hasattr(txn_df, "to_dataframe"):
                        try:
                            txn_df = txn_df.to_dataframe()
                        except Exception:
                            txn_df = None
                    else:
                        txn_df = None

                if txn_df is None or (isinstance(txn_df, pd.DataFrame) and txn_df.empty):
                    try:
                        txn_df = parsed.to_dataframe()
                    except (AttributeError, Exception):
                        txn_df = None

                if isinstance(txn_df, pd.DataFrame) and not txn_df.empty:
                    self._parse_transactions_df(txn_df, filed_date, owner_name, owner_title, rows)
                else:
                    transactions = (
                        getattr(parsed, "transactions", None)
                        or getattr(parsed, "non_derivative_transactions", None)
                        or []
                    )
                    self._parse_transactions_iter(transactions, filed_date, owner_name, owner_title, rows)

            except Exception:
                logger.debug("Failed to parse Form 4 filing for %s", symbol)
                continue

        if not rows:
            return self._empty_df()

        df = pd.DataFrame(rows, columns=_FORM4_COLUMNS)
        df.sort_values("filed_date", inplace=True)
        df.reset_index(drop=True, inplace=True)
        return df

    def _parse_transactions_df(self, txn_df, filed_date, owner_name, owner_title, rows):
        """Parse transactions from a DataFrame (newer edgartools API)."""
        code_col = _resolve_col(txn_df, "transaction_code")
        shares_col = _resolve_col(txn_df, "shares")
        price_col = _resolve_col(txn_df, "price")
        date_col = _resolve_col(txn_df, "transaction_date")
        ad_col = _resolve_col(txn_df, "acquired_disposed")
        sa_col = _resolve_col(txn_df, "shares_after")
        di_col = _resolve_col(txn_df, "direct_indirect")
        name_col = _resolve_col(txn_df, "insider_name")
        title_col = _resolve_col(txn_df, "insider_title")

        for _, row in txn_df.iterrows():
            try:
                code = str(row.get(code_col, "")) if code_col else ""
                if code not in _TRANSACTION_CODES:
                    continue

                shares_raw = float(row.get(shares_col, 0) or 0) if shares_col else 0.0
                price_raw = float(row.get(price_col, 0) or 0) if price_col else 0.0

                if price_raw == 0:
                    continue

                shares_after_raw = float(row.get(sa_col, 0) or 0) if sa_col else 0.0
                txn_date = self._parse_date(row.get(date_col) if date_col else None)

                is_direct = True
                if di_col:
                    is_direct = str(row.get(di_col, "D")) == "D"

                acquired_disposed = ""
                if ad_col:
                    acquired_disposed = str(row.get(ad_col, "") or "")
                if acquired_disposed == "D" or code == "S":
                    shares_raw = -abs(shares_raw)
                else:
                    shares_raw = abs(shares_raw)

                row_name = owner_name
                if name_col:
                    row_name = str(row.get(name_col, "") or "") or owner_name
                row_title = owner_title
                if title_col:
                    row_title = str(row.get(title_col, "") or "") or owner_title

                rows.append({
                    "filed_date": filed_date,
                    "transaction_date": txn_date or filed_date,
                    "insider_name": row_name,
                    "insider_title": row_title,
                    "transaction_type": code,
                    "shares": shares_raw,
                    "price": price_raw,
                    "shares_after": shares_after_raw,
                    "is_direct": is_direct,
                })
            except (ValueError, TypeError, AttributeError):
                continue

    def _parse_transactions_iter(self, transactions, filed_date, owner_name, owner_title, rows):
        """Parse transactions from an iterable (legacy edgartools API)."""
        for txn in transactions:
            try:
                code = getattr(txn, "transaction_code", "") or ""
                if code not in _TRANSACTION_CODES:
                    continue

                shares_raw = float(getattr(txn, "transaction_shares", 0) or 0)
                price_raw = float(getattr(txn, "transaction_price_per_share", 0) or 0)

                if price_raw == 0:
                    continue

                shares_after_raw = float(getattr(txn, "shares_owned_following", 0) or 0)
                txn_date = self._parse_date(getattr(txn, "transaction_date", None))
                is_direct = getattr(txn, "direct_or_indirect_ownership", "D") == "D"

                acquired_disposed = getattr(txn, "acquired_disposed_code", "") or ""
                if acquired_disposed == "D" or code == "S":
                    shares_raw = -abs(shares_raw)
                else:
                    shares_raw = abs(shares_raw)

                rows.append({
                    "filed_date": filed_date,
                    "transaction_date": txn_date or filed_date,
                    "insider_name": owner_name,
                    "insider_title": owner_title,
                    "transaction_type": code,
                    "shares": shares_raw,
                    "price": price_raw,
                    "shares_after": shares_after_raw,
                    "is_direct": is_direct,
                })
            except (ValueError, TypeError, AttributeError):
                continue

    @staticmethod
    def _parse_date(val) -> date | None:
        if val is None:
            return None
        if isinstance(val, date):
            return val
        try:
            return date.fromisoformat(str(val))
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _empty_df() -> pd.DataFrame:
        return pd.DataFrame(columns=_FORM4_COLUMNS)
