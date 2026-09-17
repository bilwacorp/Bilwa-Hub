"""Roles & Permissions admin UI — the permission catalog is read-only here
(seeded by migration, see core/permissions.py's ALL_PERMISSIONS). RBAC_VIEW
gates read-only endpoints (list the catalog, list roles, view a role's
permissions); RBAC_MANAGE gates every mutation (create/rename/delete a
role, change a role's permissions). Both are granted to 'admin' only at
cutover — whoever holds RBAC_MANAGE can grant themselves or anyone else
any other permission, so it's deliberately not handed out by default the
way the other resources' base permissions are."""
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import event_types as et
from app.core.permissions import RBAC_MANAGE, RBAC_VIEW, require_permission
from app.db.session import get_db
from app.models import Role
from app.schemas import PermissionOut, RoleCreate, RoleOut, RolePermissionsUpdate, RoleUpdate
from app.services import rbac
from app.services.events import record_event

router = APIRouter(prefix="/rbac", tags=["rbac"])


async def _get_role_or_404(db: AsyncSession, role_id: uuid.UUID) -> Role:
    role = await rbac.get_role_by_id(db, role_id)
    if not role:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Role not found")
    return role


def _parse_permission_strings(raw: list[str]) -> list[tuple[str, str]]:
    """"resource.action" -> (resource, action), rejecting anything that
    doesn't split cleanly into exactly two dot-separated parts."""
    parsed = []
    for s in raw:
        parts = s.split(".")
        if len(parts) != 2 or not all(parts):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Malformed permission string: {s!r}")
        parsed.append((parts[0], parts[1]))
    return parsed


@router.get("/permissions", response_model=list[PermissionOut])
async def list_permissions(db: AsyncSession = Depends(get_db), current_user=Depends(require_permission(*RBAC_VIEW))):
    return await rbac.list_permissions_catalog(db)


@router.get("/roles", response_model=list[RoleOut])
async def list_roles(db: AsyncSession = Depends(get_db), current_user=Depends(require_permission(*RBAC_VIEW))):
    return await rbac.list_roles(db)


@router.post("/roles", response_model=RoleOut, status_code=201)
async def create_role(body: RoleCreate, db: AsyncSession = Depends(get_db), current_user=Depends(require_permission(*RBAC_MANAGE))):
    role = Role(name=body.name, description=body.description, is_system=False)
    db.add(role)
    try:
        await db.flush()
    except IntegrityError:
        raise HTTPException(status.HTTP_409_CONFLICT, f"A role named {body.name!r} already exists")
    record_event(
        db, event_type=et.ROLE_CREATED, source=et.SOURCE_HUB, actor_type=et.ACTOR_STAFF, actor_id=current_user.id,
        entity_type=et.ENTITY_ROLE, entity_id=role.id, metadata={"name": role.name},
    )
    return role


@router.patch("/roles/{role_id}", response_model=RoleOut)
async def update_role(role_id: uuid.UUID, body: RoleUpdate, db: AsyncSession = Depends(get_db), current_user=Depends(require_permission(*RBAC_MANAGE))):
    role = await _get_role_or_404(db, role_id)
    if body.name is not None and body.name != role.name:
        if role.is_system:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Built-in roles can't be renamed")
        old_name = role.name
        role.name = body.name
        try:
            await db.flush()
        except IntegrityError:
            raise HTTPException(status.HTTP_409_CONFLICT, f"A role named {body.name!r} already exists")
        # Rewrite casbin_rule's p/g rows to the new name — roles.name has
        # no DB-level FK into casbin_rule (see services/rbac.py).
        await rbac.rename_role_policies(old_name, body.name)
        record_event(
            db, event_type=et.ROLE_RENAMED, source=et.SOURCE_HUB, actor_type=et.ACTOR_STAFF, actor_id=current_user.id,
            entity_type=et.ENTITY_ROLE, entity_id=role.id, metadata={"from_name": old_name, "to_name": body.name},
        )
    if body.description is not None:
        role.description = body.description
    await db.flush()
    return role


@router.delete("/roles/{role_id}", status_code=204)
async def delete_role(role_id: uuid.UUID, db: AsyncSession = Depends(get_db), current_user=Depends(require_permission(*RBAC_MANAGE))):
    role = await _get_role_or_404(db, role_id)
    if role.is_system:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Built-in roles can't be deleted")

    # If this role grants RBAC_MANAGE, make sure some *other* role still
    # would — otherwise deleting it locks every admin out of this feature
    # (and, transitively, out of ever fixing that themselves).
    role_perms = await rbac.get_permissions_for_role(role.name)
    if RBAC_MANAGE in role_perms and await rbac.would_orphan_permission(db, *RBAC_MANAGE, excluding_role=role.name):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Deleting this role would leave nobody able to manage roles and permissions",
        )

    record_event(
        db, event_type=et.ROLE_DELETED, source=et.SOURCE_HUB, actor_type=et.ACTOR_STAFF, actor_id=current_user.id,
        entity_type=et.ENTITY_ROLE, entity_id=role.id, metadata={"name": role.name, "permissions": [f"{r}.{a}" for r, a in role_perms]},
    )
    await rbac.delete_role_policies(role.name)
    await db.delete(role)
    await db.flush()


@router.get("/roles/{role_id}/permissions", response_model=list[str])
async def get_role_permissions(role_id: uuid.UUID, db: AsyncSession = Depends(get_db), current_user=Depends(require_permission(*RBAC_VIEW))):
    role = await _get_role_or_404(db, role_id)
    return [f"{r}.{a}" for r, a in await rbac.get_permissions_for_role(role.name)]


@router.put("/roles/{role_id}/permissions", response_model=list[str])
async def set_role_permissions(role_id: uuid.UUID, body: RolePermissionsUpdate, db: AsyncSession = Depends(get_db), current_user=Depends(require_permission(*RBAC_MANAGE))):
    role = await _get_role_or_404(db, role_id)
    desired = _parse_permission_strings(body.permissions)

    catalog = {(p.resource, p.action) for p in await rbac.list_permissions_catalog(db)}
    unknown = [f"{r}.{a}" for r, a in desired if (r, a) not in catalog]
    if unknown:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Not in the permission catalog: {', '.join(unknown)}")

    current = set(await rbac.get_permissions_for_role(role.name))
    if RBAC_MANAGE in current and RBAC_MANAGE not in set(desired):
        if await rbac.would_orphan_permission(db, *RBAC_MANAGE, excluding_role=role.name):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "This would leave nobody able to manage roles and permissions",
            )

    await rbac.set_role_permissions(role.name, desired)
    record_event(
        db, event_type=et.ROLE_PERMISSIONS_UPDATED, source=et.SOURCE_HUB, actor_type=et.ACTOR_STAFF,
        actor_id=current_user.id, entity_type=et.ENTITY_ROLE, entity_id=role.id,
        metadata={
            "name": role.name,
            "previous_permissions": sorted(f"{r}.{a}" for r, a in current),
            "new_permissions": sorted(f"{r}.{a}" for r, a in desired),
        },
    )
    return [f"{r}.{a}" for r, a in desired]
