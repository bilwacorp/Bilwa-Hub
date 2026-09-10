"""Pushing a maintenance window (and its siblings) to the deployment(s) it
affects. Used by api/routers/maintenance.py on create/update and by
core/maintenance_scheduler.py on auto-transition / reminder.

Each push carries the target's full current window list (a replace, not a
merge). A `notify` directive — telling the deployment to email its admins
about one window — is derived per target from `push_state`: "scheduled" the
first time a target sees a window, "reminder" once it's within
MAINTENANCE_REMINDER_HOURS of the start (each sent at most once per target).

Best-effort by design: a deployment that's unreachable is logged and left
with its `push_state` entry unset, so the next scheduler tick retries it.
Never raises past the caller."""
import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import Deployment, DeploymentStatus, MaintenanceWindow, MaintenanceWindowStatus
from app.schemas import MaintenanceWindowPublic
from app.services import deployment_client
from app.services.maintenance_query import active_windows_for

logger = logging.getLogger(__name__)


async def _targets(db: AsyncSession, window: MaintenanceWindow) -> list[Deployment]:
    if window.deployment_id is not None:
        d = (await db.execute(
            select(Deployment).where(Deployment.id == window.deployment_id)
        )).scalar_one_or_none()
        return [d] if d is not None else []
    # Fleet-wide — every registered, active deployment. `_call` itself skips
    # rows with no base_url/action_key, so filtering on status is enough.
    rows = (await db.execute(
        select(Deployment).where(Deployment.status == DeploymentStatus.active)
    )).scalars().all()
    return list(rows)


async def _public_windows_for(db: AsyncSession, deployment_id) -> list[dict]:
    windows = await active_windows_for(db, deployment_id)
    return [MaintenanceWindowPublic.model_validate(w).model_dump(mode="json") for w in windows]


def _due_notify(window: MaintenanceWindow, state: dict, now: datetime) -> Optional[str]:
    if window.status in (MaintenanceWindowStatus.completed, MaintenanceWindowStatus.cancelled):
        return None
    if not state.get("notice_at"):
        return "scheduled"
    if (
        window.status == MaintenanceWindowStatus.planned
        and not state.get("reminder_at")
        and window.scheduled_start - now <= timedelta(hours=settings.MAINTENANCE_REMINDER_HOURS)
    ):
        return "reminder"
    return None


async def sync_window(db: AsyncSession, window: MaintenanceWindow) -> None:
    """Push `window`'s deployment(s) their current full window list, deriving
    a per-target `notify` directive from `push_state`."""
    now = datetime.utcnow()
    now_iso = now.isoformat()
    changed = False
    for dep in await _targets(db, window):
        dep_key = str(dep.id)
        state = dict(window.push_state.get(dep_key, {}))
        kind = _due_notify(window, state, now)
        notify = {"window_id": str(window.id), "kind": kind} if kind else None
        try:
            await deployment_client.push_maintenance(
                dep, windows=await _public_windows_for(db, dep.id), notify=notify,
            )
        except deployment_client.DeploymentCallError as e:
            logger.warning("maintenance push to %s failed: %s", dep.slug, e)
            continue
        if kind == "scheduled":
            state["notice_at"] = now_iso
        elif kind == "reminder":
            state["reminder_at"] = now_iso
        state["last_status"] = window.status.value
        state["status_pushed_at"] = now_iso
        window.push_state = {**window.push_state, dep_key: state}
        changed = True
    if changed:
        # push_state is a JSON column — reassigning the whole dict above is
        # what makes SQLAlchemy see the mutation; flush so the caller's
        # commit persists it.
        await db.flush()


def clear_reminders(window: MaintenanceWindow) -> None:
    """Wipe every target's reminder_at — call when a window's start is moved
    earlier so a fresh reminder can fire against the new time."""
    window.push_state = {
        k: {kk: vv for kk, vv in v.items() if kk != "reminder_at"}
        for k, v in window.push_state.items()
    }
