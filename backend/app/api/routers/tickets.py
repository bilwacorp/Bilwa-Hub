import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import TICKETS_MANAGE, require_permission
from app.db.session import get_db
from app.models import Deployment, SupportTicket, SupportTicketStatus
from app.schemas import SupportTicketListResponse, SupportTicketOut, SupportTicketUpdate
from app.services import deployment_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tickets", tags=["tickets"], dependencies=[Depends(require_permission(*TICKETS_MANAGE))])


@router.get("", response_model=SupportTicketListResponse)
async def list_tickets(
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
    status_filter: SupportTicketStatus | None = Query(None, alias="status"),
    deployment_id: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    conditions = []
    if status_filter is not None:
        conditions.append(SupportTicket.status == status_filter)
    if deployment_id is not None:
        conditions.append(SupportTicket.deployment_id == deployment_id)

    total = (await db.execute(select(func.count(SupportTicket.id)).where(*conditions))).scalar() or 0
    rows = (await db.execute(
        select(SupportTicket).where(*conditions).order_by(SupportTicket.created_at.desc())
        .offset((page - 1) * page_size).limit(page_size)
    )).scalars().all()
    return SupportTicketListResponse(total=total, items=[SupportTicketOut.model_validate(t) for t in rows])


@router.get("/{ticket_id}", response_model=SupportTicketOut)
async def get_ticket(ticket_id: str, db: AsyncSession = Depends(get_db)):
    t = (await db.execute(select(SupportTicket).where(SupportTicket.id == ticket_id))).scalar_one_or_none()
    if not t:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ticket not found")
    return SupportTicketOut.model_validate(t)


@router.patch("/{ticket_id}", response_model=SupportTicketOut)
async def update_ticket(ticket_id: str, body: SupportTicketUpdate, db: AsyncSession = Depends(get_db)):
    from datetime import datetime
    t = (await db.execute(select(SupportTicket).where(SupportTicket.id == ticket_id))).scalar_one_or_none()
    if not t:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ticket not found")
    t.status = body.status
    if body.status in (SupportTicketStatus.resolved, SupportTicketStatus.closed) and t.resolved_at is None:
        t.resolved_at = datetime.utcnow()
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
