"""Resolving 'the staff who should hear about this fleet event' for
notification fan-out — every active user holding either the 'admin' or
'engineer' Casbin role (i.e. everyone with FLEET_MANAGE, see
core/permissions.py) — used for both the support-ticket-raised and
subscription-request-raised alerts."""
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.casbin_enforcer import get_enforcer
from app.core.permissions import DEFAULT_DOMAIN
from app.models import User

_FLEET_ROLES = ("admin", "engineer")


async def fleet_staff(db: AsyncSession) -> list[User]:
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
