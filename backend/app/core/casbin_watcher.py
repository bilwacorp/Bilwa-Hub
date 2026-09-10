"""Cross-worker Casbin policy sync — ported near-verbatim from
PoultryOS-CBP's core/casbin_watcher.py. Redis is a latency optimization
only; the poll loop is what guarantees convergence if Redis is down."""
import asyncio
import logging

from app.core.cache import get_redis
from app.core.casbin_enforcer import get_enforcer

logger = logging.getLogger(__name__)

_CHANNEL = "casbin_policy_reload"
_POLL_INTERVAL_SECONDS = 15

_tasks: list[asyncio.Task] = []


async def publish_reload() -> None:
    try:
        r = await get_redis()
        if r is not None:
            await r.publish(_CHANNEL, "reload")
    except Exception:
        logger.warning("casbin watcher: failed to publish reload notification", exc_info=True)


async def _subscribe_loop() -> None:
    while True:
        try:
            r = await get_redis()
            if r is None:
                await asyncio.sleep(5)
                continue
            pubsub = r.pubsub()
            await pubsub.subscribe(_CHANNEL)
            async for message in pubsub.listen():
                if message.get("type") != "message":
                    continue
                try:
                    await get_enforcer().load_policy()
                except Exception:
                    logger.warning("casbin watcher: failed to reload policy", exc_info=True)
        except Exception:
            logger.warning("casbin watcher: subscribe loop dropped, retrying", exc_info=True)
            await asyncio.sleep(5)


async def _poll_loop() -> None:
    while True:
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)
        try:
            await get_enforcer().load_policy()
        except Exception:
            logger.warning("casbin watcher: periodic reload failed", exc_info=True)


def start_watcher() -> list[asyncio.Task]:
    global _tasks
    _tasks = [asyncio.create_task(_subscribe_loop()), asyncio.create_task(_poll_loop())]
    return _tasks


def stop_watcher() -> None:
    for task in _tasks:
        task.cancel()
