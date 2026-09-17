"""The one query for 'which maintenance windows currently matter to this
deployment' — shared by the heartbeat response (api/routers/ingest.py), the
push path (services/maintenance_push.py) and the deployments-list status
derivation (api/routers/deployments.py)."""
from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import MaintenanceWindow, MaintenanceWindowStatus


async def active_windows_for(db: AsyncSession, deployment_id: Optional[UUID]) -> list[MaintenanceWindow]:
    """Non-terminal windows (planned / in_progress) that haven't ended yet,
    scoped to this deployment plus any fleet-wide (deployment_id IS NULL)
    window. Ordered by start so 'the next one' is items[0]."""
    now = datetime.utcnow()
    scope = MaintenanceWindow.deployment_id.is_(None)
    if deployment_id is not None:
        scope = or_(scope, MaintenanceWindow.deployment_id == deployment_id)
    rows = (await db.execute(
        select(MaintenanceWindow).where(
            scope,
            MaintenanceWindow.status.in_(
                [MaintenanceWindowStatus.planned, MaintenanceWindowStatus.notification, MaintenanceWindowStatus.in_progress]
            ),
            MaintenanceWindow.scheduled_end >= now,
        ).order_by(MaintenanceWindow.scheduled_start)
    )).scalars().all()
    return list(rows)


async def currently_active_windows(db: AsyncSession) -> list[MaintenanceWindow]:
    """Every window that covers `now` right now (any deployment) — used to
    derive the 'under maintenance' badge on the deployments list without an
    N+1. `in_progress`, or `planned` whose start has already passed."""
    now = datetime.utcnow()
    rows = (await db.execute(
        select(MaintenanceWindow).where(
            MaintenanceWindow.status.in_(
                [MaintenanceWindowStatus.planned, MaintenanceWindowStatus.notification, MaintenanceWindowStatus.in_progress]
            ),
            MaintenanceWindow.scheduled_start <= now,
            MaintenanceWindow.scheduled_end >= now,
        )
    )).scalars().all()
    return list(rows)
