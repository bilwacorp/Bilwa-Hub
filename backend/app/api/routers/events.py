"""Read-only surface for the OperationalEvent log (see app/models.py's
docstring and HUB-Expansion.md Phase 1). Nothing here writes an event —
every domain router/hook does that itself via services/events.py, in the
same transaction as the change it records.

Row-level visibility mirrors deployments.py/tickets.py/maintenance.py: a
caller without EVENTS_VIEW_ALL only sees events scoped to a deployment
assigned to them, plus every event with no deployment_id at all (a
registration event predates any assignment; a maintenance/approval event
tied to nothing deployment-specific isn't scoped to begin with) — same
"fleet-wide rows are never scoped" shape as maintenance.py's
deployment_id IS NULL windows."""
from typing import Optional
import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import EVENTS_VIEW, EVENTS_VIEW_ALL, has_permission, require_permission
from app.db.session import get_db
from app.models import OperationalEvent, User
from app.schemas import OperationalEventListResponse, OperationalEventOut
from app.services.deployment_scope import assigned_deployment_ids

router = APIRouter(prefix="/events", tags=["events"])


@router.get("", response_model=OperationalEventListResponse)
async def list_events(
    page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
    deployment_id: Optional[uuid.UUID] = Query(None),
    entity_type: Optional[str] = Query(None),
    event_type: Optional[str] = Query(None),
    correlation_id: Optional[uuid.UUID] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(*EVENTS_VIEW)),
):
    conditions = []
    if deployment_id is not None:
        conditions.append(OperationalEvent.deployment_id == deployment_id)
    if entity_type is not None:
        conditions.append(OperationalEvent.entity_type == entity_type)
    if event_type is not None:
        conditions.append(OperationalEvent.event_type == event_type)
    if correlation_id is not None:
        conditions.append(OperationalEvent.correlation_id == correlation_id)
    if not await has_permission(str(current_user.id), *EVENTS_VIEW_ALL):
        scoped_ids = await assigned_deployment_ids(db, current_user.id)
        conditions.append(or_(OperationalEvent.deployment_id.is_(None), OperationalEvent.deployment_id.in_(scoped_ids)))

    total = (await db.execute(select(func.count(OperationalEvent.id)).where(*conditions))).scalar() or 0
    rows = (await db.execute(
        select(OperationalEvent).where(*conditions).order_by(OperationalEvent.created_at.desc())
        .offset((page - 1) * page_size).limit(page_size)
    )).scalars().all()
    return OperationalEventListResponse(total=total, items=[OperationalEventOut.model_validate(e) for e in rows])
