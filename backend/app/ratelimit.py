"""Per-IP rate limiting (§5).

Two independent limits, both keyed on the client IP:
- new report jobs: 2 / hour (expensive — a queued Stockfish run),
- freshness cache-hit POSTs: 10 / hour (cheaper, but each costs a Lichess call).

Both limits apply to different branches of the same POST endpoint (job queued vs.
cache hit), so instead of a decorator we check them manually via the underlying
``limits`` strategy: ``strategy.hit(item, key)`` returns ``False`` when the limit
is exceeded. slowapi's ``Limiter`` supplies the storage + strategy.
"""

from __future__ import annotations

import os

from limits import RateLimitItem, parse
from slowapi import Limiter
from slowapi.util import get_remote_address

NEW_JOB_LIMIT = "2/hour"
FRESHNESS_LIMIT = "10/hour"

# In-memory storage by default; a Redis URI can be supplied so the limit is shared
# across API processes (§5 abuse guard).
_STORAGE_URI = os.environ.get("RATELIMIT_STORAGE_URI", "memory://")

limiter = Limiter(key_func=get_remote_address, storage_uri=_STORAGE_URI)

_NEW_JOB_ITEM: RateLimitItem = parse(NEW_JOB_LIMIT)
_FRESHNESS_ITEM: RateLimitItem = parse(FRESHNESS_LIMIT)


def allow_new_job(ip: str) -> bool:
    """Consume one new-job token for ``ip``; False if the 2/hour cap is hit."""
    return bool(limiter.limiter.hit(_NEW_JOB_ITEM, "new_job", ip))


def allow_freshness(ip: str) -> bool:
    """Consume one freshness token for ``ip``; False if the 10/hour cap is hit."""
    return bool(limiter.limiter.hit(_FRESHNESS_ITEM, "freshness", ip))


def reset_limits() -> None:
    """Clear all rate-limit state (used by tests)."""
    limiter.limiter.storage.reset()
