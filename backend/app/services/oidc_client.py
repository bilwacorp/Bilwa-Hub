"""Authentik OIDC client — Authorization Code + PKCE flow, id_token
verification via JWKS. Module-level discovery/JWKS caching mirrors
services/deployment_client.py's httpx-wrapper-to-external-service
convention; the difference here is there's exactly one external service
(the hub's single Authentik tenant), not one row per Deployment.

No new dependency: python-jose[cryptography] (already in requirements.txt,
already used for this hub's own HS256 session JWT and the GitHub App's
RS256 JWT, per ADR-004) verifies Authentik's RS256 id_token directly from
its JWKS dict — python-jose has no OIDC-discovery/JWKS-client of its own,
so that part is hand-rolled here with httpx (also already a dependency)."""
import base64
import hashlib
import time
from typing import Any, Optional
from urllib.parse import urlencode

import httpx
from jose import JWTError, jwt as jose_jwt

from app.core.config import settings

_HTTP_TIMEOUT = 10.0
_DISCOVERY_TTL_SECONDS = 3600
_JWKS_TTL_SECONDS = 3600
_SCOPES = "openid email profile"

_discovery_cache: dict[str, Any] = {}
_discovery_cached_at: float = 0.0
_jwks_cache: dict[str, Any] = {}
_jwks_cached_at: float = 0.0


class OidcError(Exception):
    """Any SSO-flow failure api/routers/auth.py should treat as a clean,
    user-facing login failure (redirect with ?error=...) — never a 500."""


def generate_code_challenge(code_verifier: str) -> str:
    digest = hashlib.sha256(code_verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


async def discover() -> dict:
    global _discovery_cache, _discovery_cached_at
    if _discovery_cache and (time.monotonic() - _discovery_cached_at) < _DISCOVERY_TTL_SECONDS:
        return _discovery_cache
    url = f"{settings.AUTHENTIK_ISSUER.rstrip('/')}/.well-known/openid-configuration"
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.get(url)
        resp.raise_for_status()
    except httpx.HTTPError as e:
        raise OidcError(f"Could not reach Authentik discovery endpoint: {e}") from e
    _discovery_cache = resp.json()
    _discovery_cached_at = time.monotonic()
    return _discovery_cache


async def _get_jwks(force_refresh: bool = False) -> list[dict]:
    global _jwks_cache, _jwks_cached_at
    if not force_refresh and _jwks_cache and (time.monotonic() - _jwks_cached_at) < _JWKS_TTL_SECONDS:
        return _jwks_cache.get("keys", [])
    config = await discover()
    jwks_uri = config.get("jwks_uri")
    if not jwks_uri:
        raise OidcError("Authentik discovery document has no jwks_uri")
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.get(jwks_uri)
        resp.raise_for_status()
    except httpx.HTTPError as e:
        raise OidcError(f"Could not reach Authentik JWKS endpoint: {e}") from e
    _jwks_cache = resp.json()
    _jwks_cached_at = time.monotonic()
    return _jwks_cache.get("keys", [])


async def _find_jwk(kid: str) -> dict:
    for key in await _get_jwks():
        if key.get("kid") == kid:
            return key
    # Key rotated since our last fetch — refetch once before giving up,
    # rather than caching a permanent miss for this kid.
    for key in await _get_jwks(force_refresh=True):
        if key.get("kid") == kid:
            return key
    raise OidcError(f"No matching JWKS key for kid={kid!r}")


async def build_authorization_url(*, state: str, nonce: str, code_challenge: str) -> str:
    config = await discover()
    endpoint = config.get("authorization_endpoint")
    if not endpoint:
        raise OidcError("Authentik discovery document has no authorization_endpoint")
    params = {
        "response_type": "code",
        "client_id": settings.AUTHENTIK_CLIENT_ID,
        "redirect_uri": settings.AUTHENTIK_REDIRECT_URI,
        "scope": _SCOPES,
        "state": state,
        "nonce": nonce,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return f"{endpoint}?{urlencode(params)}"


async def exchange_code_for_tokens(*, code: str, code_verifier: str) -> dict:
    config = await discover()
    token_endpoint = config.get("token_endpoint")
    if not token_endpoint:
        raise OidcError("Authentik discovery document has no token_endpoint")
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": settings.AUTHENTIK_REDIRECT_URI,
        "client_id": settings.AUTHENTIK_CLIENT_ID,
        "client_secret": settings.AUTHENTIK_CLIENT_SECRET,
        "code_verifier": code_verifier,
    }
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.post(token_endpoint, data=data)
    except httpx.HTTPError as e:
        raise OidcError(f"Could not reach Authentik token endpoint: {e}") from e
    if resp.status_code >= 400:
        raise OidcError(f"Authentik token exchange failed: {resp.status_code}: {resp.text[:300]}")
    return resp.json()


async def verify_id_token(id_token: str, *, nonce: str) -> dict:
    """Verifies signature (JWKS), iss, aud, exp/iat, and nonce; requires an
    email claim (fails closed — never falls back to preferred_username/sub,
    since email is the only field User rows are matched on)."""
    try:
        header = jose_jwt.get_unverified_header(id_token)
    except JWTError as e:
        raise OidcError(f"Malformed id_token: {e}") from e
    kid = header.get("kid")
    if not kid:
        raise OidcError("id_token header has no kid")
    key = await _find_jwk(kid)
    try:
        claims = jose_jwt.decode(
            id_token, key, algorithms=[header.get("alg", "RS256")],
            audience=settings.AUTHENTIK_CLIENT_ID, issuer=settings.AUTHENTIK_ISSUER,
        )
    except JWTError as e:
        raise OidcError(f"id_token verification failed: {e}") from e
    if claims.get("nonce") != nonce:
        raise OidcError("id_token nonce mismatch")
    if not claims.get("email"):
        raise OidcError("id_token has no email claim")
    return claims


async def build_end_session_url(*, id_token_hint: Optional[str], post_logout_redirect_uri: str) -> Optional[str]:
    """Best-effort, never raises — a discovery hiccup at logout time should
    degrade to a local-only cookie clear (api/routers/auth.py), not block
    logout."""
    try:
        config = await discover()
    except OidcError:
        return None
    endpoint = config.get("end_session_endpoint")
    if not endpoint:
        return None
    params = {"client_id": settings.AUTHENTIK_CLIENT_ID, "post_logout_redirect_uri": post_logout_redirect_uri}
    if id_token_hint:
        params["id_token_hint"] = id_token_hint
    return f"{endpoint}?{urlencode(params)}"
