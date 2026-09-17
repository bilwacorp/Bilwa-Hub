"""Background loop that drives maintenance windows — mirrors
core/casbin_watcher.py's shape (module-level task list, start/stop, a
`while True: sleep; try/except` body).

Each tick: auto-transition window status off the clock
(planned -> in_progress -> completed), then re-push every non-terminal
window to its deployment(s) — which also retries any push that failed
earlier and fires the "reminder" notify once a window is within
MAINTENANCE_REMINDER_HOURS of its start (services/maintenance_push derives
that per target).

Assumes a single hub process (the Dockerfile runs one uvicorn worker, no
Celery). If the hub ever goes multi-worker this loop needs a leader-election
guard — an in-transaction advisory lock, or a dedicated scheduler
process — but a session-level advisory lock across a mid-loop commit is
NOT it (it leaks the lock on a pooled async connection)."""
import asyncio
import logging
from datetime import datetime

from sqlalchemy import select

from app.core import event_types as et
from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.models import MaintenanceWindow, MaintenanceWindowStatus, OperationalEventStatus
from app.services.events import record_event
from app.services.maintenance_push import sync_window

logger = logging.getLogger(__name__)

_tasks: list[asyncio.Task] = []

# planned/notification are both "waiting to start" — notification is
# purely the informational flag "the advance notice already went out"
# (see services/maintenance_push.py's sync_window), not a different wait
# condition, so every clock-driven query below treats them identically.
_SCHEDULED = (MaintenanceWindowStatus.planned, MaintenanceWindowStatus.notification)


async def _auto_transition(db) -> None:
    now = datetime.utcnow()

    # HUB-Expansion.md Phase 8: an approved window has nothing left to
    # wait on — promote it to planned immediately so this same tick's
    # to_start/push-pending queries below can pick it up right away.
    approved = (await db.execute(
        select(MaintenanceWindow).where(MaintenanceWindow.status == MaintenanceWindowStatus.approved)
    )).scalars().all()
    for w in approved:
        w.status = MaintenanceWindowStatus.planned
    if approved:
        await db.flush()

    to_start = (await db.execute(
        select(MaintenanceWindow).where(
            MaintenanceWindow.status.in_(_SCHEDULED),
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
            MaintenanceWindow.status.in_(_SCHEDULED),
            MaintenanceWindow.scheduled_end <= now,
        )
    )).scalars().all()

    for w in to_start:
        w.status = MaintenanceWindowStatus.in_progress
        record_event(
            db, event_type=et.MAINTENANCE_STARTED, source=et.SOURCE_ENGINE, actor_type=et.ACTOR_SYSTEM,
            entity_type=et.ENTITY_MAINTENANCE_WINDOW, entity_id=w.id, deployment_id=w.deployment_id,
            status=OperationalEventStatus.info,
        )
    for w in (*to_finish, *expired_planned):
        w.status = MaintenanceWindowStatus.completed
        record_event(
            db, event_type=et.MAINTENANCE_COMPLETED, source=et.SOURCE_ENGINE, actor_type=et.ACTOR_SYSTEM,
            entity_type=et.ENTITY_MAINTENANCE_WINDOW, entity_id=w.id, deployment_id=w.deployment_id,
            status=OperationalEventStatus.success,
        )
    if to_start or to_finish or expired_planned:
        await db.flush()

    for w in (*to_start, *to_finish, *expired_planned):
        logger.info("maintenance window %s -> %s", w.id, w.status.value)
        # Completed windows won't be picked up by _push_pending below, so
        # push them here to release the deployment's banner/gate promptly.
        await sync_window(db, w)


async def _push_pending(db) -> None:
    """Re-push every still-relevant window: retries a failed push and fires
    the reminder notify when it comes due (both idempotent per target)."""
    now = datetime.utcnow()
    windows = (await db.execute(
        select(MaintenanceWindow).where(
            MaintenanceWindow.status.in_((*_SCHEDULED, MaintenanceWindowStatus.in_progress)),
            MaintenanceWindow.scheduled_end > now,
        )
    )).scalars().all()
    for w in windows:
        await sync_window(db, w)


async def _tick() -> None:
    async with AsyncSessionLocal() as db:
        await _auto_transition(db)
        await _push_pending(db)
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
