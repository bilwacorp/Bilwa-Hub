"""Staff login — ported/simplified from PoultryOS-CBP's auth.py: same JWT +
HttpOnly cookie shape, MFA/phone/mobile/session-CRUD all dropped (single
staff account, web-only, Phase 1 — see the plan). forgot-password/
reset-password (self-service, public/unauthenticated) were a later
addition — see their docstrings below."""
import hashlib
import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import get_current_user
from app.core.security import (
    ACCESS_TOKEN_COOKIE_NAME, create_access_token, get_password_hash, verify_password_constant_time,
)
from app.db.session import get_db
from app.models import User
from app.schemas import (
    ForgotPasswordRequest, LoginRequest, ResetPasswordConfirmRequest, TokenResponse, UserOut,
)
from app.services import rbac
from app.services.notifications.dependencies import get_notification_service
from app.services.notifications.service import NotificationService

router = APIRouter(prefix="/auth", tags=["auth"])


def _set_cookie(response: Response, token: str, ttl: timedelta) -> None:
    response.set_cookie(
        key=ACCESS_TOKEN_COOKIE_NAME, value=token, max_age=int(ttl.total_seconds()),
        httponly=True, secure=settings.APP_ENV != "development", samesite="lax", path="/api",
    )


def _clear_cookie(response: Response) -> None:
    response.delete_cookie(
        key=ACCESS_TOKEN_COOKIE_NAME, path="/api",
        secure=settings.APP_ENV != "development", samesite="lax",
    )


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, response: Response, db: AsyncSession = Depends(get_db)):
    user = (await db.execute(select(User).where(User.username == body.username))).scalar_one_or_none()
    # Constant-time check even on a lookup miss — see security.py's
    # verify_password_constant_time docstring.
    if not verify_password_constant_time(body.password, user.hashed_password if user else None) or not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect username or password")

    ttl = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    token = create_access_token(data={"sub": str(user.id), "username": user.username, "tv": user.token_version}, expires_delta=ttl)
    _set_cookie(response, token, ttl)
    return TokenResponse(expires_in=int(ttl.total_seconds()))


@router.post("/refresh", response_model=TokenResponse)
async def refresh(response: Response, current_user: User = Depends(get_current_user)):
    ttl = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    token = create_access_token(
        data={"sub": str(current_user.id), "username": current_user.username, "tv": current_user.token_version},
        expires_delta=ttl,
    )
    _set_cookie(response, token, ttl)
    return TokenResponse(expires_in=int(ttl.total_seconds()))


@router.post("/logout")
async def logout(response: Response):
    _clear_cookie(response)
    return {"status": "logged_out"}


@router.get("/me", response_model=UserOut)
async def me(current_user: User = Depends(get_current_user)):
    out = UserOut.model_validate(current_user)
    out.role = await rbac.get_role(str(current_user.id))
    return out


@router.post("/forgot-password", status_code=status.HTTP_204_NO_CONTENT)
async def forgot_password(
    body: ForgotPasswordRequest,
    db: AsyncSession = Depends(get_db),
    service: NotificationService = Depends(get_notification_service),
):
    """Always 204, whether or not the username exists, is active, or has an
    email on file — an error/success split here would let anyone enumerate
    valid staff usernames. Silently does nothing in the no-email case (same
    posture as everywhere else in this app: no address on file just means
    no send, never an error)."""
    user = (await db.execute(select(User).where(User.username == body.username))).scalar_one_or_none()
    if user and user.is_active and user.email:
        token = secrets.token_urlsafe(32)
        user.password_reset_token_hash = hashlib.sha256(token.encode()).hexdigest()
        user.password_reset_expires_at = datetime.utcnow() + timedelta(minutes=settings.PASSWORD_RESET_TOKEN_EXPIRE_MINUTES)
        await db.flush()
        reset_url = f"{settings.FRONTEND_URL.rstrip('/')}/reset-password?token={token}"
        await service.send_password_reset(
            recipient=user.email, username=user.username, reset_url=reset_url,
            expiry_minutes=settings.PASSWORD_RESET_TOKEN_EXPIRE_MINUTES,
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/reset-password", status_code=status.HTTP_204_NO_CONTENT)
async def reset_password_confirm(body: ResetPasswordConfirmRequest, db: AsyncSession = Depends(get_db)):
    token_hash = hashlib.sha256(body.token.encode()).hexdigest()
    user = (await db.execute(
        select(User).where(User.password_reset_token_hash == token_hash)
    )).scalar_one_or_none()
    if (
        not user or not user.is_active
        or not user.password_reset_expires_at or user.password_reset_expires_at < datetime.utcnow()
    ):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This reset link is invalid or has expired")

    user.hashed_password = get_password_hash(body.new_password)
    # Same as an admin-driven reset (users.py) — invalidates every JWT
    # issued before this point, and the token is single-use regardless of
    # whether it was still within its expiry window.
    user.token_version += 1
    user.password_reset_token_hash = None
    user.password_reset_expires_at = None
    await db.flush()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
