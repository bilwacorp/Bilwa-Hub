"""Public GitHub webhook endpoint — NOT Casbin-gated, same shape as
api/routers/register.py/ingest.py (see that module's comment in
core/permissions.py). Authenticated by HMAC signature instead of a staff
session or a shared-secret bearer token, since GitHub itself is the
caller. See docs/integrations/github.md's "Webhooks" section for the
full design."""
import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import event_types as et
from app.core.rate_limit import enforce_rate_limit
from app.db.session import get_db
from app.integrations.github import app_auth
from app.integrations.github.models import GitHubIntegration, GitHubIntegrationStatus, GitHubRepository, GitHubWebhookEvent
from app.integrations.github.security import verify_signature
from app.integrations.github.tasks import process_webhook_event_task
from app.models import OperationalEventStatus
from app.services import crypto
from app.services.events import record_event

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/github", tags=["github-webhooks"])


async def _accept_webhook_delivery(
    db: AsyncSession, integration: GitHubIntegration, delivery_id: Optional[str], event_type: str, payload: dict,
    *, mark_connected: bool = True,
) -> JSONResponse:
    """Shared by both webhook endpoints below once a valid signature has
    resolved a target integration — persists the delivery (the actual
    idempotency mechanism, via delivery_id's unique constraint) and
    queues the async processing task. See docs/integrations/github.md's
    "Webhooks" section. `mark_connected=False` for the one case where
    receiving a delivery does NOT mean the integration is alive — the
    `installation` webhook's own `action=deleted`, which needs its
    "disconnected" status to survive this call, not get overwritten by
    the "we just heard from it" heuristic below."""
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
    if mark_connected and integration.status != GitHubIntegrationStatus.connected:
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


@router.post("/webhooks/{integration_id}")
async def github_webhook(integration_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    # HUB-Expansion.md Phase 15 — a real GitHub org delivers at most a
    # handful of webhooks per second even under heavy activity; this is
    # only meant to blunt a flood/signature-brute-force attempt against
    # this one public URL, not to model legitimate traffic precisely.
    await enforce_rate_limit(f"github-webhook:{integration_id}", limit=120, window_seconds=60)

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

    return await _accept_webhook_delivery(db, integration, delivery_id, event_type, payload)


@router.post("/app/webhooks")
async def github_app_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """The GitHub App's single webhook URL (configured once, on the App
    itself, not per-installation — see docs/integrations/github.md's
    "GitHub App auth mode"). Verified with the one shared webhook secret
    (app_auth._load_app_credentials — DB config or env vars, whichever is
    configured), and the target GitHubIntegration is resolved from
    `payload["installation"]["id"]` instead of a URL path segment.
    Handles `installation`/`installation_repositories` directly (these
    describe the integration row itself, not a repo/PR/issue child row)
    and delegates everything else to the same delivery-insert + Celery-
    dispatch path the per-integration endpoint above uses."""
    await enforce_rate_limit("github-app-webhook", limit=120, window_seconds=60)

    raw_body = await request.body()
    signature_header = request.headers.get("x-hub-signature-256")
    delivery_id = request.headers.get("x-github-delivery")
    event_type = request.headers.get("x-github-event", "unknown")

    creds = await app_auth._load_app_credentials(db)
    secret = creds.webhook_secret if creds else None
    valid = bool(secret) and verify_signature(secret, raw_body, signature_header)

    try:
        payload = json.loads(raw_body) if raw_body else {}
    except ValueError:
        payload = {"_unparseable": True}

    if not valid:
        record_event(
            db, event_type=et.GITHUB_WEBHOOK_FAILED, source=et.SOURCE_GITHUB, actor_type=et.ACTOR_SYSTEM,
            entity_type=et.ENTITY_GITHUB_WEBHOOK_EVENT, status=OperationalEventStatus.failure,
            metadata={"event_type": event_type, "reason": "invalid_signature", "app_level": True},
        )
        return JSONResponse(status_code=401, content={"detail": "Invalid signature"})

    installation_payload = payload.get("installation") or {}
    installation_id = installation_payload.get("id")
    account_login = (installation_payload.get("account") or {}).get("login", str(installation_id))

    if event_type == "installation":
        action = payload.get("action")
        if action == "created" and installation_id is not None:
            integration = await app_auth.upsert_installation(db, installation_id, account_login, None)
            record_event(
                db, event_type=et.GITHUB_APP_INSTALLED, source=et.SOURCE_GITHUB, actor_type=et.ACTOR_SYSTEM,
                entity_type=et.ENTITY_GITHUB_INTEGRATION, entity_id=integration.id,
                metadata={"installation_id": installation_id, "github_org": account_login},
            )
            # Delegates to the same delivery-insert + Celery-dispatch path
            # every other event type uses — _handle_installation
            # (webhooks.py) does the actual repository registration from
            # this payload's `repositories` list, asynchronously.
            return await _accept_webhook_delivery(db, integration, delivery_id, event_type, payload)
        if action == "deleted" and installation_id is not None:
            integration = (
                await db.execute(select(GitHubIntegration).where(GitHubIntegration.installation_id == installation_id))
            ).scalar_one_or_none()
            if integration is None:
                return JSONResponse(status_code=200, content={"status": "uninstalled"})
            integration.status = GitHubIntegrationStatus.disconnected
            # No repositories_removed list on a `deleted` payload — every
            # repo this integration owned is inaccessible now, not just
            # some of them (see app_auth.deactivate_repositories's
            # docstring on why this deactivates rather than deletes).
            await db.execute(
                update(GitHubRepository).where(GitHubRepository.integration_id == integration.id).values(is_active=False)
            )
            record_event(
                db, event_type=et.GITHUB_APP_UNINSTALLED, source=et.SOURCE_GITHUB, actor_type=et.ACTOR_SYSTEM,
                entity_type=et.ENTITY_GITHUB_INTEGRATION, entity_id=integration.id,
                metadata={"installation_id": installation_id},
            )
            return await _accept_webhook_delivery(db, integration, delivery_id, event_type, payload, mark_connected=False)
        # permissions_accepted/suspend/unsuspend/new_permissions_accepted —
        # no HUB-side state change needed for any of these.
        return JSONResponse(status_code=200, content={"status": "ignored_action"})

    if installation_id is None:
        return JSONResponse(status_code=400, content={"detail": "Missing installation in payload"})

    integration = (
        await db.execute(select(GitHubIntegration).where(GitHubIntegration.installation_id == installation_id))
    ).scalar_one_or_none()
    if integration is None:
        # Self-heals a delivery ordering edge case (this event arrived
        # before the `installation` webhook did) rather than dropping it.
        integration = await app_auth.upsert_installation(db, installation_id, account_login, None)

    return await _accept_webhook_delivery(db, integration, delivery_id, event_type, payload)
