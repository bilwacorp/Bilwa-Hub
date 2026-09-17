"""Inbound heartbeat + support-ticket relay from a deployment — both
authenticated by hashing the presented Bearer api_key and matching
Deployment.api_key_hash (get_deployment_from_api_key), never by a
client-supplied deployment_id (see the plan's correction #4)."""
from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import event_types as et
from app.core.deps import get_deployment_from_api_key
from app.db.session import get_db
from app.models import Deployment, DeploymentSnapshot, SupportTicket
from app.schemas import (
    HeartbeatRequest, HeartbeatResponse, MaintenanceWindowPublic, SupportTicketIngest,
)
from app.services.events import record_event
from app.services.lineage import infer_release_from_heartbeat
from app.services.maintenance_query import active_windows_for
from app.services.notification_triggers import (
    new_pending_requests, notify_subscription_request_raised, notify_support_ticket_raised,
)

router = APIRouter(prefix="/ingest", tags=["ingest"])


@router.post("/heartbeat", response_model=HeartbeatResponse)
async def ingest_heartbeat(
    body: HeartbeatRequest,
    db: AsyncSession = Depends(get_db),
    deployment: Deployment = Depends(get_deployment_from_api_key),
):
    # Fetched *before* inserting the new snapshot below — this is the diff
    # base new_pending_requests() compares the incoming list against, to
    # find requests that are genuinely new rather than still-pending ones
    # carried over from the last heartbeat (see notification_triggers.py).
    previous = (await db.execute(
        select(DeploymentSnapshot.pending_requests)
        .where(DeploymentSnapshot.deployment_id == deployment.id)
        .order_by(DeploymentSnapshot.received_at.desc())
        .limit(1)
    )).scalar_one_or_none()

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
    record_event(
        db, event_type=et.DEPLOYMENT_HEARTBEAT_RECEIVED, source=et.SOURCE_DEPLOYMENT, actor_type=et.ACTOR_DEPLOYMENT,
        actor_id=deployment.id, entity_type=et.ENTITY_DEPLOYMENT, entity_id=deployment.id, deployment_id=deployment.id,
        metadata={"app_version": body.app_version},
    )
    await db.flush()
    await infer_release_from_heartbeat(db, deployment, body.app_version, snapshot.received_at)

    for request in new_pending_requests(previous, body.pending_requests):
        await notify_subscription_request_raised(db, request, deployment)

    # The heartbeat response carries this deployment's maintenance windows —
    # a self-healing resync in case a hub->deployment push was missed.
    windows = await active_windows_for(db, deployment.id)
    return HeartbeatResponse(maintenance=[MaintenanceWindowPublic.model_validate(w) for w in windows])


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
    record_event(
        db, event_type=et.TICKET_CREATED, source=et.SOURCE_DEPLOYMENT, actor_type=et.ACTOR_DEPLOYMENT,
        actor_id=deployment.id, entity_type=et.ENTITY_TICKET, entity_id=ticket.id, deployment_id=deployment.id,
        metadata={"subject": ticket.subject, "priority": ticket.priority},
    )
    await notify_support_ticket_raised(db, ticket, deployment)
    return {"ticket_id": str(ticket.id)}
