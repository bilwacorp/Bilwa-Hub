"""Staff/user management — admin-only (STAFF_MANAGE, see core/permissions.py).
Role changes go through services/rbac.py (Casbin grouping policy), not a
column on User. No hard delete: MaintenanceWindow.created_by FKs to
users.id, and deactivating (is_active=False) is enough — get_current_user
already rejects a deactivated user's token on every request, live."""
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.core.permissions import STAFF_MANAGE, require_permission
from app.core.security import get_password_hash
from app.db.session import get_db
from app.models import User
from app.schemas import (
    PasswordResetRequest, StaffUserCreate, StaffUserListResponse, StaffUserOut, StaffUserUpdate,
)
from app.services import rbac

router = APIRouter(prefix="/users", tags=["users"], dependencies=[Depends(require_permission(*STAFF_MANAGE))])


async def _get_user_or_404(db: AsyncSession, user_id: str) -> User:
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Staff user not found")
    return user


async def _out(user: User) -> StaffUserOut:
    out = StaffUserOut.model_validate(user)
    out.role = await rbac.get_role(str(user.id))
    return out


@router.get("", response_model=StaffUserListResponse)
async def list_users(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(User).order_by(User.created_at))).scalars().all()
    roles = await rbac.get_roles_by_user([str(u.id) for u in rows])
    items = []
    for u in rows:
        out = StaffUserOut.model_validate(u)
        out.role = roles.get(str(u.id))
        items.append(out)
    return StaffUserListResponse(total=len(items), items=items)


@router.post("", response_model=StaffUserOut, status_code=201)
async def create_user(body: StaffUserCreate, db: AsyncSession = Depends(get_db)):
    if not await rbac.get_role_by_name(db, body.role):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown role: {body.role!r}")
    user = User(
        username=body.username, full_name=body.full_name, email=body.email, phone=body.phone,
        hashed_password=get_password_hash(body.password),
    )
    db.add(user)
    try:
        await db.flush()
    except IntegrityError:
        raise HTTPException(status.HTTP_409_CONFLICT, "Username or email already in use")
    await rbac.set_role(str(user.id), body.role)
    return await _out(user)


@router.patch("/{user_id}", response_model=StaffUserOut)
async def update_user(user_id: uuid.UUID, body: StaffUserUpdate, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    user = await _get_user_or_404(db, user_id)
    current_role = await rbac.get_role(str(user.id))
    is_self = user.id == current_user.id
    role_changing = body.role is not None and body.role != current_role
    demoting_or_deactivating = (body.is_active is False) or role_changing

    if is_self and demoting_or_deactivating:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot deactivate or change your own role — ask another admin")

    if role_changing and not await rbac.get_role_by_name(db, body.role):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown role: {body.role!r}")

    # Generalized "don't remove the last person who can manage staff"
    # guard — checks the STAFF_MANAGE *permission*, not a hardcoded role
    # name, since roles are custom now (a role other than 'admin' could
    # also hold STAFF_MANAGE).
    current_role_perms = set(await rbac.get_permissions_for_role(current_role)) if current_role else set()
    currently_holds_staff_manage = STAFF_MANAGE in current_role_perms
    if body.is_active is False:
        would_still_hold_staff_manage = False
    elif role_changing:
        would_still_hold_staff_manage = STAFF_MANAGE in await rbac.get_permissions_for_role(body.role)
    else:
        would_still_hold_staff_manage = currently_holds_staff_manage
    losing_staff_manage = currently_holds_staff_manage and not would_still_hold_staff_manage
    if losing_staff_manage and await rbac.count_users_with_permission(db, *STAFF_MANAGE) <= 1:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Cannot remove the last staff member who can manage staff accounts")

    for field in ("full_name", "email", "phone", "is_active"):
        value = getattr(body, field)
        if value is not None:
            setattr(user, field, value)
    try:
        await db.flush()
    except IntegrityError:
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already in use")

    if body.role is not None:
        await rbac.set_role(str(user.id), body.role)
    if body.is_active is False:
        user.token_version += 1
        await db.flush()

    return await _out(user)


@router.post("/{user_id}/reset-password", status_code=204)
async def reset_password(user_id: uuid.UUID, body: PasswordResetRequest, db: AsyncSession = Depends(get_db)):
    user = await _get_user_or_404(db, user_id)
    user.hashed_password = get_password_hash(body.new_password)
    user.token_version += 1
    await db.flush()
