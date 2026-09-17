"""Maintenance-window CRUD. Every create/update also pushes the affected
deployment(s) their fresh window list (services/maintenance_push) so their
in-app banner / read-only gate track it without waiting for the next 2h
heartbeat; the maintenance_scheduler loop handles auto-transitions and
retries any push that failed here.

Row-level visibility: a caller without MAINTENANCE_VIEW_ALL only sees/
touches windows targeting a deployment assigned to them — fleet-wide
windows (deployment_id IS NULL) are never scoped, since they're not about
any one deployment (see core/permissions.py)."""
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.approvals import maintenance_hooks
from app.core import event_types as et
from app.core.permissions import (
    MAINTENANCE_CREATE, MAINTENANCE_DELETE, MAINTENANCE_UPDATE, MAINTENANCE_VIEW, MAINTENANCE_VIEW_ALL,
    has_permission, require_permission,
)
from app.db.session import get_db
from app.models import MaintenanceWindow, MaintenanceWindowStatus, OperationalEventStatus, User
from app.schemas import (
    MaintenanceWindowCreate, MaintenanceWindowListResponse, MaintenanceWindowOut, MaintenanceWindowUpdate,
)
from app.services.deployment_scope import assigned_deployment_ids
from app.services.events import record_event
from app.services.maintenance_push import clear_reminders, sync_window

router = APIRouter(prefix="/maintenance-windows", tags=["maintenance"])


async def _get_visible_or_404(db: AsyncSession, window_id: str, current_user: User) -> MaintenanceWindow:
    w = (await db.execute(select(MaintenanceWindow).where(MaintenanceWindow.id == window_id))).scalar_one_or_none()
    if not w:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Maintenance window not found")
    if w.deployment_id is not None and not await has_permission(str(current_user.id), *MAINTENANCE_VIEW_ALL):
        if w.deployment_id not in await assigned_deployment_ids(db, current_user.id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Maintenance window not found")
    return w


@router.post("", response_model=MaintenanceWindowOut, status_code=201)
async def create_window(
    body: MaintenanceWindowCreate, db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(*MAINTENANCE_CREATE)),
):
    if body.deployment_id is not None and not await has_permission(str(current_user.id), *MAINTENANCE_VIEW_ALL):
        if body.deployment_id not in await assigned_deployment_ids(db, current_user.id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Deployment not found")
    fields = body.model_dump(exclude={"save_as_draft"})
    w = MaintenanceWindow(**fields, created_by=current_user.id, status=MaintenanceWindowStatus.draft)
    db.add(w)
    await db.flush()
    await db.refresh(w)
    record_event(
        db, event_type=et.MAINTENANCE_CREATED, source=et.SOURCE_HUB, actor_type=et.ACTOR_STAFF, actor_id=current_user.id,
        entity_type=et.ENTITY_MAINTENANCE_WINDOW, entity_id=w.id, deployment_id=w.deployment_id,
        metadata={"mode": w.mode, "scheduled_start": w.scheduled_start.isoformat()},
    )
    if not body.save_as_draft:
        # HUB-Expansion.md Phase 7/8 — decides planned vs approval_required;
        # see app/approvals/maintenance_hooks.py. The common case (a plain
        # single-deployment banner/read_only window) always lands on
        # `planned` here, same as every window created before this phase.
        await maintenance_hooks.route_window(db, current_user, w)
        await sync_window(db, w)
    return MaintenanceWindowOut.model_validate(w)


@router.post("/{window_id}/submit", response_model=MaintenanceWindowOut)
async def submit_window(
    window_id: str, db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(*MAINTENANCE_CREATE)),
):
    """Moves a `draft` window through the same gate-or-schedule decision
    a non-draft create already goes through — see
    app/approvals/maintenance_hooks.route_window."""
    w = await _get_visible_or_404(db, window_id, current_user)
    if w.status != MaintenanceWindowStatus.draft:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Only a draft window can be submitted (current status: {w.status.value}).")
    await maintenance_hooks.route_window(db, current_user, w)
    await sync_window(db, w)
    return MaintenanceWindowOut.model_validate(w)


@router.get("", response_model=MaintenanceWindowListResponse)
async def list_windows(
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(*MAINTENANCE_VIEW)),
):
    conditions = []
    if not await has_permission(str(current_user.id), *MAINTENANCE_VIEW_ALL):
        scoped_ids = await assigned_deployment_ids(db, current_user.id)
        # Fleet-wide windows (deployment_id IS NULL) are always visible,
        # plus any deployment-specific window the caller is assigned to.
        conditions.append(or_(MaintenanceWindow.deployment_id.is_(None), MaintenanceWindow.deployment_id.in_(scoped_ids)))

    total = (await db.execute(select(func.count(MaintenanceWindow.id)).where(*conditions))).scalar() or 0
    rows = (await db.execute(
        select(MaintenanceWindow).where(*conditions).order_by(MaintenanceWindow.scheduled_start.desc())
        .offset((page - 1) * page_size).limit(page_size)
    )).scalars().all()
    return MaintenanceWindowListResponse(total=total, items=[MaintenanceWindowOut.model_validate(w) for w in rows])


@router.patch("/{window_id}", response_model=MaintenanceWindowOut)
async def update_window(
    window_id: str, body: MaintenanceWindowUpdate, db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(*MAINTENANCE_UPDATE)),
):
    w = await _get_visible_or_404(db, window_id, current_user)
    old_start = w.scheduled_start
    changes = body.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(w, field, value)
    if w.scheduled_start < old_start:
        # Moved earlier — let a fresh reminder fire against the new time.
        clear_reminders(w)
    # A manual status change to completed/failed gets its own specific
    # event (mirrors api/routers/tickets.py's update_ticket swapping
    # TICKET_RESOLVED in for TICKET_UPDATED) — completed/failed usually
    # arrives here from the scheduler instead (core/maintenance_scheduler
    # .py), but staff can also mark either by hand (e.g. `failed` has no
    # automatic trigger at all, see MaintenanceWindowStatus's docstring).
    new_status = changes.get("status")
    event_type, event_status = {
        MaintenanceWindowStatus.completed: (et.MAINTENANCE_COMPLETED, OperationalEventStatus.success),
        MaintenanceWindowStatus.failed: (et.MAINTENANCE_FAILED, OperationalEventStatus.failure),
    }.get(new_status, (et.MAINTENANCE_UPDATED, OperationalEventStatus.info))
    record_event(
        db, event_type=event_type, source=et.SOURCE_HUB, actor_type=et.ACTOR_STAFF, actor_id=current_user.id,
        entity_type=et.ENTITY_MAINTENANCE_WINDOW, entity_id=w.id, deployment_id=w.deployment_id,
        status=event_status, metadata={"changed_fields": list(changes.keys())},
    )
    await db.flush()
    # Push the updated list to every affected deployment — a cancelled or
    # rescheduled window drops out of / changes in active_windows_for, so
    # the deployment prunes or updates its local copy.
    await sync_window(db, w)
    return MaintenanceWindowOut.model_validate(w)


@router.delete("/{window_id}", status_code=204)
async def delete_window(
    window_id: str, db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(*MAINTENANCE_DELETE)),
):
    w = await _get_visible_or_404(db, window_id, current_user)
    # Cancel-then-push while the row still exists (so it drops out of
    # active_windows_for and deployments prune it), then hard-delete.
    w.status = MaintenanceWindowStatus.cancelled
    record_event(
        db, event_type=et.MAINTENANCE_DELETED, source=et.SOURCE_HUB, actor_type=et.ACTOR_STAFF, actor_id=current_user.id,
        entity_type=et.ENTITY_MAINTENANCE_WINDOW, entity_id=w.id, deployment_id=w.deployment_id,
    )
    await db.flush()
    await sync_window(db, w)
    await db.delete(w)
