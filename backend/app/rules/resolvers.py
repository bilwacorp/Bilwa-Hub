"""Approver resolution — the two strategies a rule action of type
`assign_approver` can use, tried by resolve() in priority order:
casbin_role first, then explicit_users as a fallback for special cases
(hardcoded user ids are exactly what this engine exists to avoid).

Ported from PoultryPro-CBF's app/rules/resolvers.py with the
branch_manager and linked_supervisor strategies dropped (no branch or
batch/farm-supervisor concept in this hub) — see rules/models.py's
ApproverStrategy.

Inactive users are always filtered out — routing a task to a deactivated
login would create an unactionable, permanently-pending approval.
"""
import uuid
from typing import Optional, Set

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.casbin_enforcer import get_enforcer
from app.core.permissions import DEFAULT_DOMAIN
from app.models import User
from app.rules.models import ApproverStrategy, RuleAction


async def _active_user_ids(db: AsyncSession, user_ids: Set[uuid.UUID]) -> Set[uuid.UUID]:
    if not user_ids:
        return set()
    result = await db.execute(select(User.id).where(User.id.in_(user_ids), User.is_active.is_(True)))
    return set(result.scalars().all())


async def resolve_casbin_role(db: AsyncSession, role_name: Optional[str]) -> Set[uuid.UUID]:
    """Every user holding `role_name` (a Casbin g-policy in the default
    domain)."""
    if not role_name:
        return set()
    raw_ids = await get_enforcer().get_users_for_role_in_domain(role_name, DEFAULT_DOMAIN)
    ids = {uuid.UUID(u) for u in raw_ids}
    return await _active_user_ids(db, ids)


async def resolve_explicit_users(db: AsyncSession, user_ids: Optional[list]) -> Set[uuid.UUID]:
    if not user_ids:
        return set()
    ids = {uuid.UUID(u) for u in user_ids}
    return await _active_user_ids(db, ids)


async def resolve(
    db: AsyncSession,
    action: RuleAction,
    *,
    business_object_type: str,
    business_object_id: uuid.UUID,
) -> Set[uuid.UUID]:
    """Resolve the candidate approver set for one `assign_approver` rule
    action. If the action names a specific strategy, only that strategy
    runs. If it doesn't, both are tried in priority order and the first
    non-empty result wins."""
    if action.strategy is not None:
        strategies = [action.strategy]
    else:
        strategies = [ApproverStrategy.casbin_role, ApproverStrategy.explicit_users]

    for strategy in strategies:
        if strategy == ApproverStrategy.casbin_role:
            ids = await resolve_casbin_role(db, action.role_name)
        elif strategy == ApproverStrategy.explicit_users:
            ids = await resolve_explicit_users(db, action.user_ids)
        else:
            ids = set()
        if ids:
            return ids
    return set()
