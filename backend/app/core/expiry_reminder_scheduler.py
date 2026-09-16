"""Background loop that checks each deployment's latest-known subscription
expiry_date and fires a one-time "expiring soon" email/WhatsApp alert once
it's within EXPIRY_REMINDER_DAYS_BEFORE days out — mirrors
core/maintenance_scheduler.py's shape (module-level task list, start/stop,
a `while True: sleep; try/except` body).

Idempotent via Deployment.expiry_reminder_sent_for: only fires once per
distinct expiry_date value, so it never nags on every tick, and a renewal
that changes expiry_date (reflected on the next heartbeat) naturally makes
a future reminder eligible again with no explicit reset.

Assumes a single hub process (see maintenance_scheduler.py's docstring for
the same caveat if this ever goes multi-worker)."""
import asyncio
import logging
from datetime import date, timedelta

from sqlalchemy import select

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.models import Deployment, DeploymentSnapshot
from app.services.notification_triggers import notify_subscription_expiring

logger = logging.getLogger(__name__)

_tasks: list[asyncio.Task] = []


async def _latest_expiry(db, deployment_id) -> date | None:
    return (await db.execute(
        select(DeploymentSnapshot.expiry_date)
        .where(DeploymentSnapshot.deployment_id == deployment_id)
        .order_by(DeploymentSnapshot.received_at.desc())
        .limit(1)
    )).scalar_one_or_none()


async def _tick() -> None:
    async with AsyncSessionLocal() as db:
        deployments = (await db.execute(select(Deployment))).scalars().all()
        today = date.today()
        for d in deployments:
            expiry = await _latest_expiry(db, d.id)
            if expiry is None:
                continue
            days_left = (expiry - today).days
            if 0 <= days_left <= settings.EXPIRY_REMINDER_DAYS_BEFORE and d.expiry_reminder_sent_for != expiry:
                logger.info("deployment %s expiry reminder: %s days left (expires %s)", d.id, days_left, expiry)
                await notify_subscription_expiring(db, d, expiry, days_left)
                d.expiry_reminder_sent_for = expiry
        await db.commit()


async def _loop() -> None:
    while True:
        await asyncio.sleep(settings.EXPIRY_REMINDER_SCHEDULER_INTERVAL_SECONDS)
        try:
            await _tick()
        except Exception:
            logger.warning("expiry reminder scheduler: tick failed", exc_info=True)


def start_scheduler() -> list[asyncio.Task]:
    global _tasks
    _tasks = [asyncio.create_task(_loop())]
    return _tasks


def stop_scheduler() -> None:
    for task in _tasks:
        task.cancel()
