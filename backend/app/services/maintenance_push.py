"""Pushing a maintenance window (and its siblings) to the deployment(s) it
affects. Used by api/routers/maintenance.py on create/update and by
core/maintenance_scheduler.py on auto-transition / reminder.

Best-effort by design: a deployment that's unreachable is logged and left
with its `push_state` entry unset, so the next scheduler tick retries it.
Never raises past the caller."""
import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Deployment, DeploymentStatus, MaintenanceWindow
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


async def sync_window(db: AsyncSession, window: MaintenanceWindow, *, notify_kind: str | None = None) -> None:
    """Push `window`'s deployment(s) their current full window list. If
    `notify_kind` ("scheduled" | "reminder") is given, ask each target that
    hasn't already been notified for that kind to email its admins, and
    record it in `push_state`."""
    now_iso = datetime.utcnow().isoformat()
    changed = False
    for dep in await _targets(db, window):
        dep_key = str(dep.id)
        state = dict(window.push_state.get(dep_key, {}))
        notify = None
        if notify_kind == "scheduled" and not state.get("notice_at"):
            notify = {"window_id": str(window.id), "kind": "scheduled"}
        elif notify_kind == "reminder" and not state.get("reminder_at"):
            notify = {"window_id": str(window.id), "kind": "reminder"}
        try:
            await deployment_client.push_maintenance(
                dep, windows=await _public_windows_for(db, dep.id), notify=notify,
            )
        except deployment_client.DeploymentCallError as e:
            logger.warning("maintenance push to %s failed: %s", dep.slug, e)
            continue
        if notify and notify["kind"] == "scheduled":
            state["notice_at"] = now_iso
        elif notify and notify["kind"] == "reminder":
            state["reminder_at"] = now_iso
        state["last_status"] = window.status.value
        state["status_pushed_at"] = now_iso
        window.push_state = {**window.push_state, dep_key: state}
        changed = True
    if changed:
        # push_state is a JSON column — reassigning the whole dict above is
        # what makes SQLAlchemy see the mutation; flush so the loop's single
        # commit persists it.
        await db.flush()
