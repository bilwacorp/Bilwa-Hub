"""Resolving 'the staff who should hear about this fleet event' for
notification fan-out."""
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.casbin_enforcer import get_enforcer
from app.core.permissions import (
    DEFAULT_DOMAIN, DEPLOYMENTS_MANAGE, MAINTENANCE_MANAGE, NOTIFICATIONS_MANAGE, TICKETS_MANAGE,
)
from app.models import DeploymentStaffAssignment, User

# Every active user holding *any* of these — not a hardcoded 'admin'/
# 'engineer' role-name check, which broke down the moment custom roles
# existed (migration 010_rbac_catalog): a new role scoped to just
# tickets.manage would've silently never heard about anything. Checking
# permissions directly means any role an admin grants fleet-area access to
# is automatically included, with no extra wiring.
_FLEET_PERMISSIONS = (DEPLOYMENTS_MANAGE, TICKETS_MANAGE, MAINTENANCE_MANAGE, NOTIFICATIONS_MANAGE)


async def fleet_staff(db: AsyncSession) -> list[User]:
    """Every active user who can reach at least one fleet-area permission
    through whatever role they hold — the fallback used by
    recipients_for_deployment below when a deployment has no staff
    explicitly assigned."""
    enforcer = get_enforcer()
    role_names: set[str] = set()
    for resource, action in _FLEET_PERMISSIONS:
        role_names.update(row[0] for row in enforcer.get_filtered_policy(1, DEFAULT_DOMAIN, resource, action))
    user_ids: set[uuid.UUID] = set()
    for role in role_names:
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
