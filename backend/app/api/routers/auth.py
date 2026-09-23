"""Staff login via Authentik OIDC (Authorization Code + PKCE) — replaced
the old username/password flow entirely (see docs/adr/ADR-012-authentik-
sso.md). No auto-provisioning: /callback matches the id_token's `email`
claim against an existing, active `User` row — an admin must have
pre-created it. Once that match succeeds, this hub still mints its own
signed session JWT/cookie exactly as before, so everything downstream
(core/deps.py's get_current_user, Casbin/RBAC) is unaffected by this
change."""
import secrets
from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import get_current_user
from app.core.security import ACCESS_TOKEN_COOKIE_NAME, create_access_token
from app.db.session import get_db
from app.models import User
from app.schemas import LogoutResponse, TokenResponse, UserOut
from app.services import oidc_client, rbac

router = APIRouter(prefix="/auth", tags=["auth"])

_SSO_FLOW_COOKIE_NAME = "sso_flow"
_SSO_FLOW_COOKIE_TTL_SECONDS = 300  # the whole Authentik round-trip has 5 minutes to complete
_SSO_ID_TOKEN_COOKIE_NAME = "sso_id_token"


def _cookie_flags() -> dict:
    return {"secure": settings.APP_ENV != "development", "samesite": "lax"}


def _set_session_cookie(response: Response, token: str, ttl: timedelta) -> None:
    response.set_cookie(
        key=ACCESS_TOKEN_COOKIE_NAME, value=token, max_age=int(ttl.total_seconds()),
        httponly=True, path="/api", **_cookie_flags(),
    )


def _clear_session_cookies(response: Response) -> None:
    response.delete_cookie(key=ACCESS_TOKEN_COOKIE_NAME, path="/api", **_cookie_flags())
    response.delete_cookie(key=_SSO_ID_TOKEN_COOKIE_NAME, path="/api", **_cookie_flags())


@router.get("/login")
async def login(response: Response):
    """Starts the Authentik round-trip — a real browser navigation (302),
    never an XHR/fetch target. state/nonce/PKCE code_verifier are packed
    into one short-lived HttpOnly cookie scoped to /api/v1/auth: with no
    per-device session store in this app (Phase 1 posture, and REDIS_URL
    is optional/best-effort elsewhere), the cookie itself — HttpOnly,
    Secure, narrow-path, 5-minute TTL — is the CSRF boundary, compared
    against the `state` query param /callback receives back."""
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    code_verifier = secrets.token_urlsafe(64)
    code_challenge = oidc_client.generate_code_challenge(code_verifier)

    auth_url = await oidc_client.build_authorization_url(state=state, nonce=nonce, code_challenge=code_challenge)
    redirect = RedirectResponse(auth_url, status_code=status.HTTP_302_FOUND)
    redirect.set_cookie(
        key=_SSO_FLOW_COOKIE_NAME, value=f"{state}.{nonce}.{code_verifier}",
        max_age=_SSO_FLOW_COOKIE_TTL_SECONDS, httponly=True, path="/api/v1/auth", **_cookie_flags(),
    )
    return redirect


@router.get("/callback")
async def callback(
    request: Request,
    code: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Authentik redirects the browser here after sign-in. Any failure —
    missing/mismatched state, a bad code, an id_token that fails
    verification, or no matching active User — redirects to the frontend
    login page with a clean ?error=, never a raw 4xx/5xx: the browser is
    mid-navigation, so there's no XHR caller to hand a status code to."""
    flow_cookie = request.cookies.get(_SSO_FLOW_COOKIE_NAME)
    error_redirect = RedirectResponse(f"{settings.FRONTEND_URL.rstrip('/')}/login?error=sso_failed")
    error_redirect.delete_cookie(key=_SSO_FLOW_COOKIE_NAME, path="/api/v1/auth", **_cookie_flags())

    if not code or not state or not flow_cookie:
        return error_redirect
    try:
        expected_state, nonce, code_verifier = flow_cookie.split(".", 2)
    except ValueError:
        return error_redirect
    if not secrets.compare_digest(expected_state, state):
        return error_redirect

    try:
        tokens = await oidc_client.exchange_code_for_tokens(code=code, code_verifier=code_verifier)
        id_token = tokens.get("id_token")
        if not id_token:
            return error_redirect
        claims = await oidc_client.verify_id_token(id_token, nonce=nonce)
    except oidc_client.OidcError:
        return error_redirect

    user = (await db.execute(select(User).where(User.email == claims["email"]))).scalar_one_or_none()
    if not user or not user.is_active:
        no_account_redirect = RedirectResponse(f"{settings.FRONTEND_URL.rstrip('/')}/login?error=no_account")
        no_account_redirect.delete_cookie(key=_SSO_FLOW_COOKIE_NAME, path="/api/v1/auth", **_cookie_flags())
        return no_account_redirect

    ttl = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    token = create_access_token(data={"sub": str(user.id), "username": user.username, "tv": user.token_version}, expires_delta=ttl)

    success_redirect = RedirectResponse(f"{settings.FRONTEND_URL.rstrip('/')}/sso/complete")
    _set_session_cookie(success_redirect, token, ttl)
    success_redirect.set_cookie(
        key=_SSO_ID_TOKEN_COOKIE_NAME, value=id_token, max_age=int(ttl.total_seconds()),
        httponly=True, path="/api", **_cookie_flags(),
    )
    success_redirect.delete_cookie(key=_SSO_FLOW_COOKIE_NAME, path="/api/v1/auth", **_cookie_flags())
    return success_redirect


@router.post("/refresh", response_model=TokenResponse)
async def refresh(response: Response, current_user: User = Depends(get_current_user)):
    ttl = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    token = create_access_token(
        data={"sub": str(current_user.id), "username": current_user.username, "tv": current_user.token_version},
        expires_delta=ttl,
    )
    _set_session_cookie(response, token, ttl)
    return TokenResponse(expires_in=int(ttl.total_seconds()))


@router.post("/logout", response_model=LogoutResponse)
async def logout(request: Request, response: Response):
    """Clears both local cookies and, when Authentik's discovery document
    has one, hands back an end_session_endpoint URL — the frontend does a
    full browser redirect there so the Authentik SSO session actually
    ends too, not just this hub's local one (otherwise a staff member
    could click 'Sign in with Authentik' right back in without ever
    seeing a credential prompt, since Authentik's own session cookie
    would still be live)."""
    id_token = request.cookies.get(_SSO_ID_TOKEN_COOKIE_NAME)
    _clear_session_cookies(response)
    sso_logout_url = await oidc_client.build_end_session_url(
        id_token_hint=id_token,
        post_logout_redirect_uri=f"{settings.FRONTEND_URL.rstrip('/')}/login",
    )
    return LogoutResponse(status="logged_out", sso_logout_url=sso_logout_url)


@router.get("/me", response_model=UserOut)
async def me(current_user: User = Depends(get_current_user)):
    out = UserOut.model_validate(current_user)
    out.role = await rbac.get_role(str(current_user.id))
    # The frontend gates UI elements with can(resource, action) against
    # this instead of a hardcoded role-name check (see
    # services/rbac.get_permissions_for_user).
    out.permissions = [f"{r}.{a}" for r, a in await rbac.get_permissions_for_user(str(current_user.id))]
    return out
