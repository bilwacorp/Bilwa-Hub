"""Row-level visibility helper shared by deployments.py/tickets.py/
maintenance.py — see core/permissions.py's "Row-level visibility" section
for the full rationale. A user with no DeploymentStaffAssignment rows and
no `<resource>.view_all` simply sees nothing in any of those three."""
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DeploymentStaffAssignment


async def assigned_deployment_ids(db: AsyncSession, user_id: uuid.UUID) -> set[uuid.UUID]:
    rows = (await db.execute(
        select(DeploymentStaffAssignment.deployment_id).where(DeploymentStaffAssignment.user_id == user_id)
    )).scalars().all()
    return set(rows)
