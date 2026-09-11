"""Staff login — ported/simplified from PoultryOS-CBP's auth.py: same JWT +
HttpOnly cookie shape, MFA/phone/mobile/session-CRUD all dropped (single
staff account, web-only, Phase 1 — see the plan)."""
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import get_current_user
from app.core.security import (
    ACCESS_TOKEN_COOKIE_NAME, create_access_token, verify_password_constant_time,
)
from app.db.session import get_db
from app.models import User
from app.schemas import LoginRequest, TokenResponse, UserOut
from app.services import rbac

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
