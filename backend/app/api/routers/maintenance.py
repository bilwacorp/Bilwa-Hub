"""Plain CRUD for maintenance windows — no automation/reminders in Phase 1
(see the plan)."""
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.core.permissions import STAFF_MANAGE, require_permission
from app.db.session import get_db
from app.models import MaintenanceWindow, User
from app.schemas import (
    MaintenanceWindowCreate, MaintenanceWindowListResponse, MaintenanceWindowOut, MaintenanceWindowUpdate,
)

router = APIRouter(
    prefix="/maintenance-windows", tags=["maintenance"],
    dependencies=[Depends(require_permission(*STAFF_MANAGE))],
)


@router.post("", response_model=MaintenanceWindowOut, status_code=201)
async def create_window(
    body: MaintenanceWindowCreate, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user),
):
    w = MaintenanceWindow(**body.model_dump(), created_by=current_user.id)
    db.add(w)
    await db.flush()
    await db.refresh(w)
    return MaintenanceWindowOut.model_validate(w)


@router.get("", response_model=MaintenanceWindowListResponse)
async def list_windows(
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), db: AsyncSession = Depends(get_db),
):
    total = (await db.execute(select(func.count(MaintenanceWindow.id)))).scalar() or 0
    rows = (await db.execute(
        select(MaintenanceWindow).order_by(MaintenanceWindow.scheduled_start.desc())
        .offset((page - 1) * page_size).limit(page_size)
    )).scalars().all()
    return MaintenanceWindowListResponse(total=total, items=[MaintenanceWindowOut.model_validate(w) for w in rows])


@router.patch("/{window_id}", response_model=MaintenanceWindowOut)
async def update_window(window_id: str, body: MaintenanceWindowUpdate, db: AsyncSession = Depends(get_db)):
    w = (await db.execute(select(MaintenanceWindow).where(MaintenanceWindow.id == window_id))).scalar_one_or_none()
    if not w:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Maintenance window not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(w, field, value)
    await db.flush()
    return MaintenanceWindowOut.model_validate(w)


@router.delete("/{window_id}", status_code=204)
async def delete_window(window_id: str, db: AsyncSession = Depends(get_db)):
    w = (await db.execute(select(MaintenanceWindow).where(MaintenanceWindow.id == window_id))).scalar_one_or_none()
    if not w:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Maintenance window not found")
    await db.delete(w)
