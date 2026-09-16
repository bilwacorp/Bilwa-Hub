"""Public GitHub webhook endpoint — NOT Casbin-gated, same shape as
api/routers/register.py/ingest.py (see that module's comment in
core/permissions.py). Authenticated by HMAC signature instead of a staff
session or a shared-secret bearer token, since GitHub itself is the
caller. See docs/integrations/github.md's "Webhooks" section for the
full design."""
import json
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import event_types as et
from app.db.session import get_db
from app.integrations.github.models import GitHubIntegration, GitHubIntegrationStatus, GitHubWebhookEvent
from app.integrations.github.security import verify_signature
from app.integrations.github.tasks import process_webhook_event_task
from app.models import OperationalEventStatus
from app.services import crypto
from app.services.events import record_event

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/github", tags=["github-webhooks"])


@router.post("/webhooks/{integration_id}")
async def github_webhook(integration_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    # Raw bytes first — signature verification needs the exact bytes
    # GitHub signed, not a re-serialized JSON object (see security.py).
    raw_body = await request.body()
    signature_header = request.headers.get("x-hub-signature-256")
    delivery_id = request.headers.get("x-github-delivery")
    event_type = request.headers.get("x-github-event", "unknown")

    integration = await db.get(GitHubIntegration, integration_id)
    if integration is None:
        # 404 here reveals nothing sensitive (no signature to defend, no
        # row to describe) — unlike a deployment_id, a GitHub integration
        # id isn't something a caller could be probing for confidential
        # per-customer data.
        return JSONResponse(status_code=404, content={"detail": "Unknown integration"})

    secret = crypto.decrypt(integration.webhook_secret_encrypted) if integration.webhook_secret_encrypted else None
    valid = bool(secret) and verify_signature(secret, raw_body, signature_header)

    try:
        payload = json.loads(raw_body) if raw_body else {}
    except ValueError:
        payload = {"_unparseable": True}

    if not valid:
        record_event(
            db, event_type=et.GITHUB_WEBHOOK_FAILED, source=et.SOURCE_GITHUB, actor_type=et.ACTOR_SYSTEM,
            entity_type=et.ENTITY_GITHUB_WEBHOOK_EVENT, deployment_id=None, status=OperationalEventStatus.failure,
            metadata={"integration_id": str(integration.id), "event_type": event_type, "reason": "invalid_signature"},
        )
        # Returned, not raised — a raised HTTPException would make the
        # get_db dependency roll back the whole session, discarding the
        # audit event above along with it.
        return JSONResponse(status_code=401, content={"detail": "Invalid signature"})

    if not delivery_id:
        return JSONResponse(status_code=400, content={"detail": "Missing X-GitHub-Delivery header"})

    webhook_event = GitHubWebhookEvent(
        integration_id=integration.id, delivery_id=delivery_id, event_type=event_type,
        payload=payload, signature_valid=True,
    )
    db.add(webhook_event)
    try:
        await db.flush()
    except IntegrityError:
        # GitHub's own redelivery-on-timeout behavior — the actual
        # idempotency mechanism is this unique constraint, not a
        # best-effort pre-check (see docs/integrations/github.md).
        await db.rollback()
        return JSONResponse(status_code=200, content={"status": "duplicate_delivery"})

    integration.last_webhook_at = webhook_event.received_at
    if integration.status != GitHubIntegrationStatus.connected:
        integration.status = GitHubIntegrationStatus.connected
    record_event(
        db, event_type=et.GITHUB_WEBHOOK_RECEIVED, source=et.SOURCE_GITHUB, actor_type=et.ACTOR_SYSTEM,
        entity_type=et.ENTITY_GITHUB_WEBHOOK_EVENT, entity_id=webhook_event.id,
        correlation_id=webhook_event.correlation_id, status=OperationalEventStatus.info,
        metadata={"integration_id": str(integration.id), "event_type": event_type, "delivery_id": delivery_id},
    )
    await db.flush()

    process_webhook_event_task.delay(str(webhook_event.id))
    return JSONResponse(status_code=202, content={"status": "accepted", "webhook_event_id": str(webhook_event.id)})
