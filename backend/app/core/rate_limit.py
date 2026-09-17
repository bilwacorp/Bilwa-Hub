"""HUB-Expansion.md Phase 15 — "rate limiting," previously flagged (Phase
0 audit, Phase 3's own security review) with "no existing precedent in
this codebase for rate-limiting middleware." Backed by the same Redis
app/core/cache.py already uses for Casbin cross-worker policy sync —
fails OPEN (allows the request through) if Redis is unreachable, the
same "Redis is a latency optimization, never a hard dependency" posture
casbin_watcher.py already has. This is defense-in-depth layered on top
of each endpoint's real authentication (HMAC signature for GitHub
webhooks, a single-use token for registration) — never the only
control, and never applied to a Casbin-gated staff-facing route (a
logged-in staff member hammering their own UI is not the threat model
here)."""
import logging

from fastapi import HTTPException, status

from app.core.cache import get_redis

logger = logging.getLogger(__name__)


async def enforce_rate_limit(key: str, *, limit: int, window_seconds: int) -> None:
    """Fixed-window counter keyed by `key` (e.g. an integration id, or a
    client IP) — simple over a sliding-window/token-bucket scheme,
    matching the scope of what's actually needed here: making a brute-
    force/flood attempt against a public endpoint materially harder, not
    precise quota accounting."""
    r = await get_redis()
    if r is None:
        return
    try:
        redis_key = f"ratelimit:{key}"
        count = await r.incr(redis_key)
        if count == 1:
            await r.expire(redis_key, window_seconds)
    except Exception:
        logger.warning("rate_limit: Redis error, failing open for key=%s", key, exc_info=True)
        return
    if count > limit:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests")
