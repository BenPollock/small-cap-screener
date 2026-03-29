"""SEC EDGAR rate limiting: 10 req/sec, User-Agent required.

Adapted from claude-backtester/src/backtester/data/edgar_utils.py for
retry/backoff logic on SEC rate limit errors (HTTP 403/429).

The SEC requires:
- Max 10 requests/second
- User-Agent header with name and email
- Exponential backoff on rate limit responses
"""

import logging
import time
from functools import wraps

logger = logging.getLogger(__name__)

# SEC rate limit: 10 requests per second
MAX_REQUESTS_PER_SECOND = 10
MIN_REQUEST_INTERVAL = 1.0 / MAX_REQUESTS_PER_SECOND  # 0.1 seconds

# Retry settings for rate limit errors
DEFAULT_MAX_RETRIES = 3
DEFAULT_INITIAL_BACKOFF = 10.0  # seconds


class RateLimiter:
    """Token bucket rate limiter for SEC EDGAR API.

    Enforces max 10 requests/second with smooth spacing.
    """

    def __init__(self, max_per_second: int = MAX_REQUESTS_PER_SECOND):
        self._interval = 1.0 / max_per_second
        self._last_request_time = 0.0

    def wait(self) -> None:
        """Wait if necessary to stay within rate limit."""
        elapsed = time.time() - self._last_request_time
        if elapsed < self._interval:
            time.sleep(self._interval - elapsed)
        self._last_request_time = time.time()


def is_rate_limit_error(exc: Exception) -> bool:
    """Check if an exception is a SEC rate-limit error.

    Adapted from claude-backtester/src/backtester/data/edgar_utils.py
    """
    exc_str = str(exc).lower()

    if "403" in exc_str or "429" in exc_str:
        return True
    if "too many requests" in exc_str:
        return True
    if "rate limit" in exc_str:
        return True

    cls_name = type(exc).__name__.lower()
    if "toomanyrequest" in cls_name or "ratelimit" in cls_name:
        return True

    return False


def edgar_retry(max_retries=DEFAULT_MAX_RETRIES, initial_backoff=DEFAULT_INITIAL_BACKOFF):
    """Decorator that retries on SEC rate-limit errors with exponential backoff.

    Adapted from claude-backtester/src/backtester/data/edgar_utils.py
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as exc:
                    if not is_rate_limit_error(exc):
                        raise
                    last_exc = exc
                    if attempt < max_retries:
                        delay = initial_backoff * (2 ** attempt)
                        logger.warning(
                            "SEC rate limit hit (attempt %d/%d), retrying in %.0fs: %s",
                            attempt + 1, max_retries + 1, delay, exc,
                        )
                        time.sleep(delay)
                    else:
                        logger.warning(
                            "SEC rate limit: max retries (%d) exhausted: %s",
                            max_retries + 1, exc,
                        )
            raise last_exc  # type: ignore[misc]
        return wrapper
    return decorator
