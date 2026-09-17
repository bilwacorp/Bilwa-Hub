"""Public registration handshake — see the plan's "Registration + heartbeat
flow". Token-authenticated, not JWT: a deployment calls this exactly once,
using the single-use registration_token BilwaCorp minted for it (see
api/routers/deployments.py's create_deployment)."""
import hashlib
import secrets
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import event_types as et
from app.core.rate_limit import enforce_rate_limit
from app.core.url_safety import assert_hostname_resolves_publicly
from app.db.session import get_db
from app.models import Deployment, DeploymentStatus
from app.schemas import RegisterRequest, RegisterResponse
from app.services import crypto
from app.services.events import record_event

router = APIRouter(tags=["register"])


@router.post("/register", response_model=RegisterResponse)
async def register(body: RegisterRequest, request: Request, db: AsyncSession = Depends(get_db)):
    # HUB-Expansion.md Phase 15 — public, single-use-token-guarded, but
    # the token itself is guessable-by-brute-force in principle; rate
    # limit by caller IP as a second layer. See core/rate_limit.py.
    client_ip = request.headers.get("x-real-ip") or (request.client.host if request.client else "unknown")
    await enforce_rate_limit(f"register:{client_ip}", limit=10, window_seconds=60)

    token_hash = hashlib.sha256(body.registration_token.encode()).hexdigest()
    deployment = (await db.execute(
        select(Deployment).where(Deployment.registration_token_hash == token_hash)
    )).scalar_one_or_none()

    if deployment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown registration token")
    if deployment.registration_token_consumed_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Registration token already consumed")

    # HUB-Expansion.md Phase 15 — the async DNS-resolution half of the SSRF
    # check (RegisterRequest.base_url's own Pydantic validator already did
    # the cheap scheme/format/literal-IP check); done only once the token
    # is confirmed valid, so an unauthenticated caller can't use this to
    # fish for information about arbitrary hostnames.
    try:
        await assert_hostname_resolves_publicly(body.base_url)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))

    api_key = secrets.token_urlsafe(32)
    deployment.api_key_hash = hashlib.sha256(api_key.encode()).hexdigest()
    deployment.action_key_encrypted = crypto.encrypt(body.action_key)
    deployment.base_url = body.base_url
    deployment.status = DeploymentStatus.active
    deployment.registration_token_consumed_at = datetime.utcnow()
    record_event(
        db, event_type=et.DEPLOYMENT_REGISTERED, source=et.SOURCE_DEPLOYMENT, actor_type=et.ACTOR_DEPLOYMENT,
        actor_id=deployment.id, entity_type=et.ENTITY_DEPLOYMENT, entity_id=deployment.id, deployment_id=deployment.id,
        metadata={"base_url": deployment.base_url},
    )
    await db.flush()

    return RegisterResponse(deployment_id=deployment.id, api_key=api_key)
