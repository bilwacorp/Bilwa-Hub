"""Public registration handshake — see the plan's "Registration + heartbeat
flow". Token-authenticated, not JWT: a deployment calls this exactly once,
using the single-use registration_token BilwaCorp minted for it (see
api/routers/deployments.py's create_deployment)."""
import hashlib
import secrets
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models import Deployment, DeploymentStatus
from app.schemas import RegisterRequest, RegisterResponse
from app.services import crypto

router = APIRouter(tags=["register"])


@router.post("/register", response_model=RegisterResponse)
async def register(body: RegisterRequest, db: AsyncSession = Depends(get_db)):
    token_hash = hashlib.sha256(body.registration_token.encode()).hexdigest()
    deployment = (await db.execute(
        select(Deployment).where(Deployment.registration_token_hash == token_hash)
    )).scalar_one_or_none()

    if deployment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown registration token")
    if deployment.registration_token_consumed_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Registration token already consumed")

    api_key = secrets.token_urlsafe(32)
    deployment.api_key_hash = hashlib.sha256(api_key.encode()).hexdigest()
    deployment.action_key_encrypted = crypto.encrypt(body.action_key)
    deployment.base_url = body.base_url
    deployment.status = DeploymentStatus.active
    deployment.registration_token_consumed_at = datetime.utcnow()
    await db.flush()

    return RegisterResponse(deployment_id=deployment.id, api_key=api_key)
