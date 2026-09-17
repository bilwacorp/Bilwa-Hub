import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import event_types as et
from app.core.permissions import (
    TICKETS_MANAGE_LINKS, TICKETS_UPDATE_STATUS, TICKETS_VIEW, TICKETS_VIEW_ALL, has_permission, require_permission,
)
from app.db.session import get_db
from app.models import Deployment, SupportTicket, SupportTicketLink, SupportTicketStatus, User
from app.schemas import (
    OperationalEventOut, SupportTicketListResponse, SupportTicketLinkCreate, SupportTicketLinkOut, SupportTicketOut,
    SupportTicketUpdate,
)
from app.services import deployment_client
from app.services.deployment_scope import assigned_deployment_ids
from app.services.events import record_event
from app.services.ticket_links import resolve_link_display, target_exists, ticket_timeline

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tickets", tags=["tickets"])


async def _get_visible_or_404(db: AsyncSession, ticket_id: str, current_user: User) -> SupportTicket:
    t = (await db.execute(select(SupportTicket).where(SupportTicket.id == ticket_id))).scalar_one_or_none()
    if not t:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ticket not found")
    if not await has_permission(str(current_user.id), *TICKETS_VIEW_ALL):
        if t.deployment_id not in await assigned_deployment_ids(db, current_user.id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Ticket not found")
    return t


@router.get("", response_model=SupportTicketListResponse)
async def list_tickets(
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
    status_filter: SupportTicketStatus | None = Query(None, alias="status"),
    deployment_id: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(*TICKETS_VIEW)),
):
    conditions = []
    if status_filter is not None:
        conditions.append(SupportTicket.status == status_filter)
    if deployment_id is not None:
        conditions.append(SupportTicket.deployment_id == deployment_id)
    if not await has_permission(str(current_user.id), *TICKETS_VIEW_ALL):
        scoped_ids = await assigned_deployment_ids(db, current_user.id)
        if not scoped_ids:
            return SupportTicketListResponse(total=0, items=[])
        conditions.append(SupportTicket.deployment_id.in_(scoped_ids))

    total = (await db.execute(select(func.count(SupportTicket.id)).where(*conditions))).scalar() or 0
    rows = (await db.execute(
        select(SupportTicket).where(*conditions).order_by(SupportTicket.created_at.desc())
        .offset((page - 1) * page_size).limit(page_size)
    )).scalars().all()
    return SupportTicketListResponse(total=total, items=[SupportTicketOut.model_validate(t) for t in rows])


@router.get("/{ticket_id}", response_model=SupportTicketOut)
async def get_ticket(
    ticket_id: str, db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(*TICKETS_VIEW)),
):
    t = await _get_visible_or_404(db, ticket_id, current_user)
    return SupportTicketOut.model_validate(t)


@router.patch("/{ticket_id}", response_model=SupportTicketOut)
async def update_ticket(
    ticket_id: str, body: SupportTicketUpdate, db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(*TICKETS_UPDATE_STATUS)),
):
    from datetime import datetime
    t = await _get_visible_or_404(db, ticket_id, current_user)
    old_status = t.status
    t.status = body.status
    if body.status in (SupportTicketStatus.resolved, SupportTicketStatus.closed) and t.resolved_at is None:
        t.resolved_at = datetime.utcnow()
    record_event(
        db,
        event_type=et.TICKET_RESOLVED if body.status in (SupportTicketStatus.resolved, SupportTicketStatus.closed) else et.TICKET_UPDATED,
        source=et.SOURCE_HUB, actor_type=et.ACTOR_STAFF, actor_id=current_user.id,
        entity_type=et.ENTITY_TICKET, entity_id=t.id, deployment_id=t.deployment_id,
        metadata={"from_status": old_status.value, "to_status": body.status.value},
    )
    await db.flush()

    # Best-effort: tell the deployment so it can show the new status back to
    # the client admin who raised it. Never fails the staff-facing request —
    # same "hub outage/deployment outage must never block the other side"
    # convention as every other cross-service call here.
    deployment = (await db.execute(select(Deployment).where(Deployment.id == t.deployment_id))).scalar_one_or_none()
    if deployment:
        try:
            await deployment_client.push_ticket_status(
                deployment, hub_ticket_id=str(t.id), status=t.status.value,
                resolved_at=t.resolved_at.isoformat() if t.resolved_at else None,
            )
        except deployment_client.DeploymentCallError:
            logger.warning("tickets.update_ticket: push to deployment %s failed", deployment.id, exc_info=True)

    return SupportTicketOut.model_validate(t)


# ── ticket links + timeline (HUB-Expansion.md Phase 6 — see
# app/services/ticket_links.py) ─────────────────────────────────────────────

async def _link_out(db: AsyncSession, link: SupportTicketLink) -> SupportTicketLinkOut:
    label, url, target_status = await resolve_link_display(db, link)
    return SupportTicketLinkOut(
        id=link.id, ticket_id=link.ticket_id, link_type=link.link_type, target_id=link.target_id,
        created_by=link.created_by, created_at=link.created_at, label=label, url=url, target_status=target_status,
    )


@router.get("/{ticket_id}/links", response_model=list[SupportTicketLinkOut])
async def list_ticket_links(
    ticket_id: str, db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(*TICKETS_VIEW)),
):
    t = await _get_visible_or_404(db, ticket_id, current_user)
    rows = (await db.execute(
        select(SupportTicketLink).where(SupportTicketLink.ticket_id == t.id).order_by(SupportTicketLink.created_at)
    )).scalars().all()
    return [await _link_out(db, link) for link in rows]


@router.post("/{ticket_id}/links", response_model=SupportTicketLinkOut, status_code=201)
async def create_ticket_link(
    ticket_id: str, body: SupportTicketLinkCreate, db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(*TICKETS_MANAGE_LINKS)),
):
    t = await _get_visible_or_404(db, ticket_id, current_user)
    if not await target_exists(db, body.link_type, body.target_id):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown {body.link_type.value} id")

    link = SupportTicketLink(
        ticket_id=t.id, link_type=body.link_type, target_id=body.target_id, created_by=current_user.id,
    )
    db.add(link)
    try:
        await db.flush()
    except IntegrityError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This link already exists on this ticket")
    await db.refresh(link)

    record_event(
        db, event_type=et.TICKET_LINK_ADDED, source=et.SOURCE_HUB, actor_type=et.ACTOR_STAFF, actor_id=current_user.id,
        entity_type=et.ENTITY_TICKET, entity_id=t.id, deployment_id=t.deployment_id,
        metadata={"link_type": body.link_type.value, "target_id": str(body.target_id)},
    )
    return await _link_out(db, link)


@router.delete("/{ticket_id}/links/{link_id}", status_code=204)
async def delete_ticket_link(
    ticket_id: str, link_id: str, db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(*TICKETS_MANAGE_LINKS)),
):
    t = await _get_visible_or_404(db, ticket_id, current_user)
    link = (await db.execute(
        select(SupportTicketLink).where(SupportTicketLink.id == link_id, SupportTicketLink.ticket_id == t.id)
    )).scalar_one_or_none()
    if link is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Link not found")

    record_event(
        db, event_type=et.TICKET_LINK_REMOVED, source=et.SOURCE_HUB, actor_type=et.ACTOR_STAFF, actor_id=current_user.id,
        entity_type=et.ENTITY_TICKET, entity_id=t.id, deployment_id=t.deployment_id,
        metadata={"link_type": link.link_type.value, "target_id": str(link.target_id)},
    )
    await db.delete(link)
    await db.flush()


@router.get("/{ticket_id}/timeline", response_model=list[OperationalEventOut])
async def get_ticket_timeline(
    ticket_id: str, db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(*TICKETS_VIEW)),
):
    t = await _get_visible_or_404(db, ticket_id, current_user)
    events = await ticket_timeline(db, t)
    return [OperationalEventOut.model_validate(e) for e in events]
