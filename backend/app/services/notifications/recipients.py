"""Resolving 'the staff who should hear about this fleet event' for
notification fan-out."""
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.casbin_enforcer import get_enforcer
from app.core.permissions import DEFAULT_DOMAIN
from app.models import DeploymentStaffAssignment, User

_FLEET_ROLES = ("admin", "engineer")


async def fleet_staff(db: AsyncSession) -> list[User]:
    """Every active user holding either the 'admin' or 'engineer' Casbin
    role (i.e. everyone with FLEET_MANAGE, see core/permissions.py) — the
    fleet-wide fallback used by recipients_for_deployment below when a
    deployment has no staff assigned."""
    enforcer = get_enforcer()
    user_ids: set[uuid.UUID] = set()
    for role in _FLEET_ROLES:
        for raw_id in await enforcer.get_users_for_role_in_domain(role, DEFAULT_DOMAIN):
            try:
                user_ids.add(uuid.UUID(raw_id))
            except ValueError:
                continue
    if not user_ids:
        return []
    result = await db.execute(
        select(User).where(User.id.in_(user_ids), User.is_active.is_(True))
    )
    return list(result.scalars().all())


async def recipients_for_deployment(db: AsyncSession, deployment_id: uuid.UUID) -> list[User]:
    """The actual notification target list for one deployment's ticket/
    subscription-request alerts: staff explicitly assigned to it
    (api/routers/deployments.py's PUT .../staff), if any — otherwise every
    fleet_staff() holder, so an unassigned deployment still notifies
    everyone rather than going silent."""
    assigned = (await db.execute(
        select(User)
        .join(DeploymentStaffAssignment, DeploymentStaffAssignment.user_id == User.id)
        .where(DeploymentStaffAssignment.deployment_id == deployment_id, User.is_active.is_(True))
    )).scalars().all()
    if assigned:
        return list(assigned)
    return await fleet_staff(db)
