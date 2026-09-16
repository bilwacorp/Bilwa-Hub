"""Resolving 'the staff who should hear about this fleet event' for
notification fan-out."""
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.casbin_enforcer import get_enforcer
from app.core.permissions import DEFAULT_DOMAIN, DEPLOYMENTS_VIEW_ALL
from app.models import DeploymentStaffAssignment, User


async def fleet_staff(db: AsyncSession) -> list[User]:
    """Every active user holding DEPLOYMENTS_VIEW_ALL — the fallback used
    by recipients_for_deployment below when a deployment has no staff
    explicitly assigned. Deliberately *not* "everyone with any fleet-area
    permission": since migration 011, a role without view_all can't reach
    an unassigned deployment's page (or its tickets/requests) at all — so
    notifying such a user about it would send them a link to something
    they'd 404 on. Only view_all holders can actually see an unassigned
    deployment, so only they're notified about one."""
    enforcer = get_enforcer()
    role_names = {row[0] for row in enforcer.get_filtered_policy(1, DEFAULT_DOMAIN, *DEPLOYMENTS_VIEW_ALL)}
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
