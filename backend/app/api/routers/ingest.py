"""Inbound heartbeat + support-ticket relay from a deployment — both
authenticated by hashing the presented Bearer api_key and matching
Deployment.api_key_hash (get_deployment_from_api_key), never by a
client-supplied deployment_id (see the plan's correction #4)."""
from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_deployment_from_api_key
from app.db.session import get_db
from app.models import Deployment, DeploymentSnapshot, SupportTicket
from app.schemas import HeartbeatRequest, SupportTicketIngest

router = APIRouter(prefix="/ingest", tags=["ingest"])


@router.post("/heartbeat")
async def ingest_heartbeat(
    body: HeartbeatRequest,
    db: AsyncSession = Depends(get_db),
    deployment: Deployment = Depends(get_deployment_from_api_key),
):
    sub = body.subscription
    snapshot = DeploymentSnapshot(
        deployment_id=deployment.id,
        app_version=body.app_version,
        plan_name=sub.plan_name if sub else None,
        subscription_status=sub.status if sub else None,
        expiry_date=sub.expiry_date if sub else None,
        trial_ends_at=sub.trial_ends_at if sub else None,
        auto_renew=sub.auto_renew if sub else None,
        usage=[u.model_dump() for u in body.usage],
        pending_requests=[r.model_dump(mode="json") for r in body.pending_requests],
        # received_at deliberately left to its column default
        # (datetime.utcnow at insert) — never trust the client's clock.
    )
    db.add(snapshot)
    await db.flush()
    return {"status": "ok"}


@router.post("/support-ticket")
async def ingest_support_ticket(
    body: SupportTicketIngest,
    db: AsyncSession = Depends(get_db),
    deployment: Deployment = Depends(get_deployment_from_api_key),
):
    ticket = SupportTicket(
        deployment_id=deployment.id,
        subject=body.subject,
        description=body.description,
        priority=body.priority,
        submitted_by_name=body.submitted_by_name,
        submitted_by_email=body.submitted_by_email,
        created_at=datetime.utcnow(),
    )
    db.add(ticket)
    await db.flush()
    await db.refresh(ticket)
    return {"ticket_id": str(ticket.id)}
