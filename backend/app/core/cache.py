"""Redis helper — ported from PoultryOS-CBP's core/cache.py. Degrades
gracefully: if Redis is down, callers (casbin_watcher.py) fall back to
their own poll loop."""
import logging
import time

logger = logging.getLogger(__name__)

_redis = None
_last_failure: float = 0.0
_RETRY_COOLDOWN_SECONDS = 15


async def get_redis():
    global _redis, _last_failure
    if _redis is not None:
        try:
            await _redis.ping()
            return _redis
        except Exception:
            _redis = None
            logger.debug("Redis ping failed, will attempt to reconnect", exc_info=True)

    if time.time() - _last_failure < _RETRY_COOLDOWN_SECONDS:
        return None

    try:
        import redis.asyncio as aioredis
        from app.core.config import settings
        _redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True, socket_connect_timeout=1)
        await _redis.ping()
    except Exception:
        _redis = None
        _last_failure = time.time()
        logger.debug("Redis connection failed", exc_info=True)
        return None
    return _redis
