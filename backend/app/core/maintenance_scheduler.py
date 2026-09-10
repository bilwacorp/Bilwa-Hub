"""Background loop that drives maintenance windows — mirrors
core/casbin_watcher.py's shape (module-level task list, start/stop, a
`while True: sleep; try/except` body).

Phase 1: auto-transition window status off the clock
(planned -> in_progress -> completed) and push each change to the affected
deployment(s). Phase 2 adds reminder pushes here.

Assumes a single hub process (the Dockerfile runs one uvicorn worker, no
Celery). If the hub ever goes multi-worker this loop needs a leader-election
guard — an in-transaction advisory lock, or a dedicated scheduler
process — but a session-level advisory lock across a mid-loop commit is
NOT it (it leaks the lock on a pooled async connection)."""
import asyncio
import logging
from datetime import datetime

from sqlalchemy import select

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.models import MaintenanceWindow, MaintenanceWindowStatus
from app.services.maintenance_push import sync_window

logger = logging.getLogger(__name__)

_tasks: list[asyncio.Task] = []


async def _auto_transition(db) -> None:
    now = datetime.utcnow()

    to_start = (await db.execute(
        select(MaintenanceWindow).where(
            MaintenanceWindow.status == MaintenanceWindowStatus.planned,
            MaintenanceWindow.scheduled_start <= now,
            MaintenanceWindow.scheduled_end > now,
        )
    )).scalars().all()
    to_finish = (await db.execute(
        select(MaintenanceWindow).where(
            MaintenanceWindow.status == MaintenanceWindowStatus.in_progress,
            MaintenanceWindow.scheduled_end <= now,
        )
    )).scalars().all()
    # A planned window whose end has already passed (whole window missed) —
    # skip straight to completed.
    expired_planned = (await db.execute(
        select(MaintenanceWindow).where(
            MaintenanceWindow.status == MaintenanceWindowStatus.planned,
            MaintenanceWindow.scheduled_end <= now,
        )
    )).scalars().all()

    for w in to_start:
        w.status = MaintenanceWindowStatus.in_progress
    for w in (*to_finish, *expired_planned):
        w.status = MaintenanceWindowStatus.completed
    if to_start or to_finish or expired_planned:
        await db.flush()

    for w in (*to_start, *to_finish, *expired_planned):
        logger.info("maintenance window %s -> %s", w.id, w.status.value)
        await sync_window(db, w)


async def _tick() -> None:
    async with AsyncSessionLocal() as db:
        await _auto_transition(db)
        await db.commit()


async def _loop() -> None:
    while True:
        await asyncio.sleep(settings.MAINTENANCE_SCHEDULER_INTERVAL_SECONDS)
        try:
            await _tick()
        except Exception:
            logger.warning("maintenance scheduler: tick failed", exc_info=True)


def start_scheduler() -> list[asyncio.Task]:
    global _tasks
    _tasks = [asyncio.create_task(_loop())]
    return _tasks


def stop_scheduler() -> None:
    for task in _tasks:
        task.cancel()
