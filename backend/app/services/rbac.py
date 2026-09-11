"""Staff role assignment — a thin layer over the Casbin enforcer's
domain-aware RBAC API (`core/casbin_enforcer.py`). Casbin's `casbin_rule`
table is the source of truth for who has which role; `User` carries no
`role` column. One user has at most one role in this phase — `set_role`
replaces whatever grouping row exists rather than adding a second one."""
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.casbin_enforcer import get_enforcer
from app.core.casbin_watcher import publish_reload
from app.core.permissions import DEFAULT_DOMAIN
from app.models import User


async def get_role(user_id: str) -> str | None:
    roles = await get_enforcer().get_roles_for_user_in_domain(user_id, DEFAULT_DOMAIN)
    return roles[0] if roles else None


async def get_roles_by_user(user_ids: list[str]) -> dict[str, str | None]:
    """Batch form of get_role for list endpoints — one pass over the
    in-memory policy instead of one enforcer call per row."""
    enforcer = get_enforcer()
    return {uid: (await enforcer.get_roles_for_user_in_domain(uid, DEFAULT_DOMAIN) or [None])[0] for uid in user_ids}


async def set_role(user_id: str, role: str) -> None:
    enforcer = get_enforcer()
    # "" (field 1 wildcard) matches any existing role for this user+domain,
    # not just one specific value — see delete_roles_for_user_in_domain's
    # remove_filtered_grouping_policy(0, user, role, domain) implementation.
    await enforcer.delete_roles_for_user_in_domain(user_id, "", DEFAULT_DOMAIN)
    await enforcer.add_role_for_user_in_domain(user_id, role, DEFAULT_DOMAIN)
    # This worker's own enforcer is already up to date (the calls above
    # mutate its in-memory policy directly) — publish_reload only needs to
    # reach *other* workers/processes.
    await publish_reload()


async def count_active_admins(db: AsyncSession) -> int:
    admin_ids = await get_enforcer().get_users_for_role_in_domain("admin", DEFAULT_DOMAIN)
    if not admin_ids:
        return 0
    result = await db.execute(
        select(func.count(User.id)).where(User.id.in_([uuid.UUID(i) for i in admin_ids]), User.is_active.is_(True))
    )
    return result.scalar() or 0
