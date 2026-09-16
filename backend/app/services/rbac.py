"""Staff role assignment + the Roles & Permissions admin feature
(api/routers/rbac.py) — a thin layer over the Casbin enforcer's
domain-aware RBAC API (`core/casbin_enforcer.py`). Casbin's `casbin_rule`
table is the source of truth for who has which role and what a role can
do; `Role`/`Permission` (app/models.py) are DB-backed metadata the admin
UI renders against, kept in sync with casbin_rule by the functions below
(never edited directly). One user has at most one role — `set_role`
replaces whatever grouping row exists rather than adding a second one."""
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.casbin_enforcer import get_enforcer
from app.core.casbin_watcher import publish_reload
from app.core.permissions import DEFAULT_DOMAIN
from app.models import Permission, Role, User


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


async def get_permissions_for_role(role_name: str) -> list[tuple[str, str]]:
    """Every (resource, action) a role's `p` policy rows grant — not
    filtered against the `permissions` catalog, so a role can (in
    principle) hold a policy for a resource/action that has no catalog
    row; the admin UI just won't have a checkbox for it."""
    rows = get_enforcer().get_filtered_policy(0, role_name, DEFAULT_DOMAIN)
    return [(row[2], row[3]) for row in rows]


async def get_permissions_for_user(user_id: str) -> list[tuple[str, str]]:
    """Every (resource, action) this specific user can reach through
    whatever role(s) they hold — used by /auth/me so the frontend can gate
    UI elements with can(resource, action) instead of a hardcoded role name
    (which breaks down once custom roles exist)."""
    rows = get_enforcer().get_permissions_for_user_in_domain(user_id, DEFAULT_DOMAIN)
    return [(row[2], row[3]) for row in rows]


async def set_role_permissions(role_name: str, permissions: list[tuple[str, str]]) -> None:
    """Replaces a role's full permission set wholesale (diffed against what
    it currently holds, so unrelated policy metadata isn't churned)."""
    enforcer = get_enforcer()
    current = set(await get_permissions_for_role(role_name))
    desired = set(permissions)
    for resource, action in desired - current:
        await enforcer.add_policy(role_name, DEFAULT_DOMAIN, resource, action)
    for resource, action in current - desired:
        await enforcer.remove_policy(role_name, DEFAULT_DOMAIN, resource, action)
    await publish_reload()


async def rename_role_policies(old_name: str, new_name: str) -> None:
    """Casbin has no rename primitive — rewrite every p row (this role's
    grants) and g row (every user assigned to it) that reference the old
    name. `roles.name` has no DB-level FK into casbin_rule (v0 is a free
    string), so this is the only thing keeping them in sync on a rename."""
    enforcer = get_enforcer()
    for row in enforcer.get_filtered_policy(0, old_name):
        await enforcer.remove_policy(*row)
        await enforcer.add_policy(new_name, *row[1:])
    for row in enforcer.get_filtered_grouping_policy(1, old_name):
        await enforcer.remove_grouping_policy(*row)
        await enforcer.add_grouping_policy(row[0], new_name, row[2])
    await publish_reload()


async def delete_role_policies(role_name: str) -> None:
    """Strips every p row (the role's grants) and g row (every user
    assigned to it) — called right before deleting the Role metadata row.
    Any user who only held this role loses all access until reassigned
    (surfaced as a warning in the delete-role UI)."""
    enforcer = get_enforcer()
    await enforcer.remove_filtered_policy(0, role_name, DEFAULT_DOMAIN)
    await enforcer.remove_filtered_grouping_policy(1, role_name, DEFAULT_DOMAIN)
    await publish_reload()


async def count_users_with_permission(db: AsyncSession, resource: str, action: str) -> int:
    """How many active users can currently reach (resource, action) through
    any role — the lockout guard for role/permission edits uses this (e.g.
    "don't let the last staff:manage or rbac:manage holder lose it")
    instead of hardcoding a role name, since roles are custom now."""
    enforcer = get_enforcer()
    role_names = {row[0] for row in enforcer.get_filtered_policy(1, DEFAULT_DOMAIN, resource, action)}
    if not role_names:
        return 0
    user_ids: set[uuid.UUID] = set()
    for role_name in role_names:
        for raw_id in await enforcer.get_users_for_role_in_domain(role_name, DEFAULT_DOMAIN):
            try:
                user_ids.add(uuid.UUID(raw_id))
            except ValueError:
                continue
    if not user_ids:
        return 0
    result = await db.execute(
        select(func.count(User.id)).where(User.id.in_(user_ids), User.is_active.is_(True))
    )
    return result.scalar() or 0


async def would_orphan_permission(db: AsyncSession, resource: str, action: str, *, excluding_role: str) -> bool:
    """True if, after `excluding_role` stops granting (resource, action), no
    active user could still reach it through any *other* role. Assumes one
    role per user (this hub's model — no need to worry about a user holding
    it via a second role) — used before deleting a role or stripping a
    permission from one, so RBAC_MANAGE in particular can never be
    orphaned entirely."""
    enforcer = get_enforcer()
    role_names = {row[0] for row in enforcer.get_filtered_policy(1, DEFAULT_DOMAIN, resource, action)}
    role_names.discard(excluding_role)
    if not role_names:
        return True
    user_ids: set[uuid.UUID] = set()
    for role_name in role_names:
        for raw_id in await enforcer.get_users_for_role_in_domain(role_name, DEFAULT_DOMAIN):
            try:
                user_ids.add(uuid.UUID(raw_id))
            except ValueError:
                continue
    if not user_ids:
        return True
    result = await db.execute(
        select(func.count(User.id)).where(User.id.in_(user_ids), User.is_active.is_(True))
    )
    return (result.scalar() or 0) == 0


# ── Role / Permission metadata CRUD (Role/Permission tables) ───────────────

async def list_permissions_catalog(db: AsyncSession) -> list[Permission]:
    return list((await db.execute(select(Permission).order_by(Permission.resource, Permission.action))).scalars().all())


async def list_roles(db: AsyncSession) -> list[Role]:
    return list((await db.execute(select(Role).order_by(Role.name))).scalars().all())


async def get_role_by_id(db: AsyncSession, role_id) -> Role | None:
    return (await db.execute(select(Role).where(Role.id == role_id))).scalar_one_or_none()


async def get_role_by_name(db: AsyncSession, name: str) -> Role | None:
    return (await db.execute(select(Role).where(Role.name == name))).scalar_one_or_none()
