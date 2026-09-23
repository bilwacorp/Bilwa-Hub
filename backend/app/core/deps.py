"""Ported from PoultryOS-CBP's core/deps.py: _BearerOrCookie + get_current_user
(token_version revocation check). Deliberately DROPPED for Phase 1: the
UserSession/"sid" per-device revocation machinery — with exactly one staff
account and no multi-device session-management UI planned yet, it's
complexity with no user-facing payoff right now. token_version alone still
lets a password change invalidate every outstanding token."""
import hashlib
import uuid
from typing import Optional

from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import ACCESS_TOKEN_COOKIE_NAME, decode_token
from app.db.session import get_db
from app.models import Deployment, User


class _BearerOrCookie(OAuth2PasswordBearer):
    async def __call__(self, request: Request) -> Optional[str]:
        header_token = await super().__call__(request)
        if header_token:
            return header_token
        return request.cookies.get(ACCESS_TOKEN_COOKIE_NAME)


oauth2_scheme = _BearerOrCookie(tokenUrl="/api/v1/auth/callback", auto_error=False)


async def get_current_user(
    token: Optional[str] = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if not token:
        raise credentials_exception
    payload = decode_token(token)
    if not payload:
        raise credentials_exception
    user_id = payload.get("sub")
    if not user_id:
        raise credentials_exception

    result = await db.execute(select(User).where(User.id == uuid.UUID(user_id)))
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        raise credentials_exception
    if payload.get("tv", 0) != user.token_version:
        raise credentials_exception
    return user


async def get_deployment_from_api_key(
    authorization: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db),
) -> Deployment:
    """Gates api/routers/ingest.py — identity comes ONLY from hashing the
    presented Bearer token and matching Deployment.api_key_hash, per the
    plan's correction: the request body never carries a deployment_id, so
    there's no chance of an id/key mismatch being silently accepted."""
    credentials_exception = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or missing api_key")
    if not authorization or not authorization.lower().startswith("bearer "):
        raise credentials_exception
    api_key = authorization[7:]
    api_key_hash = hashlib.sha256(api_key.encode()).hexdigest()
    result = await db.execute(select(Deployment).where(Deployment.api_key_hash == api_key_hash))
    deployment = result.scalar_one_or_none()
    if not deployment:
        raise credentials_exception
    return deployment
