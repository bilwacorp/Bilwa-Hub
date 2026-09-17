"""GitHub App auth mode — the App-level JWT (signs as the App itself) and
the installation access token it's used to mint (acts as one specific
installation, scoped to whatever repos that installation was granted).
See docs/integrations/github.md's "GitHub App auth mode" section and
docs/adr/ADR-004-github-app-auth.md for the full design.

One GitHub App serves every GitHubIntegration row with
auth_mode=github_app — app credentials are Settings (core/config.py),
not per-integration DB columns, mirroring HUB_ENCRYPTION_KEY/WHATSAPP_*'s
"env-level, optional feature" tier."""
import time
from datetime import datetime, timedelta
from typing import Optional

import httpx
from jose import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.integrations.github.client import _API_BASE, _HTTP_TIMEOUT, GitHubApiError, GitHubConnectionError, GitHubRateLimitError
from app.integrations.github.models import GitHubAuthMode, GitHubIntegration, GitHubIntegrationStatus, GitHubRepository
from app.services import crypto

# GitHub rejects an App JWT with more than a 10-minute lifetime; 9 minutes
# plus a 60s backdated `iat` (clock-skew tolerance, GitHub's own
# recommendation) leaves margin on both ends.
_APP_JWT_TTL_SECONDS = 9 * 60
# Refresh the cached installation token this long before its real expiry
# (GitHub tokens live 1h) — avoids a request racing an expiry that lands
# mid-call.
_TOKEN_REFRESH_BUFFER_SECONDS = 120


def _require_app_configured() -> None:
    if not settings.GITHUB_APP_ID or not settings.GITHUB_APP_PRIVATE_KEY:
        raise GitHubApiError("GitHub App is not configured on this hub (GITHUB_APP_ID/GITHUB_APP_PRIVATE_KEY)")


def _app_jwt() -> str:
    _require_app_configured()
    now = int(time.time())
    payload = {"iat": now - 60, "exp": now + _APP_JWT_TTL_SECONDS, "iss": settings.GITHUB_APP_ID}
    return jwt.encode(payload, settings.GITHUB_APP_PRIVATE_KEY, algorithm="RS256")


async def _app_request(method: str, path: str) -> dict:
    try:
        async with httpx.AsyncClient(base_url=_API_BASE, timeout=_HTTP_TIMEOUT) as http_client:
            resp = await http_client.request(
                method, path,
                headers={
                    "Authorization": f"Bearer {_app_jwt()}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
    except httpx.HTTPError as e:
        raise GitHubConnectionError(f"{method} {path} -> connection failed: {e}") from e

    if resp.status_code in (403, 429):
        raise GitHubRateLimitError(f"{method} {path} -> rate limited ({resp.status_code})")
    if resp.status_code >= 400:
        raise GitHubApiError(f"{method} {path} -> {resp.status_code}: {resp.text[:300]}")
    return resp.json()


async def get_installation_info(installation_id: int) -> dict:
    """GET /app/installations/{id} — used by the callback/webhook to read
    the installed account's login, as the App itself (not as any one
    installation's token)."""
    return await _app_request("GET", f"/app/installations/{installation_id}")


async def get_installation_token(db: AsyncSession, integration: GitHubIntegration) -> str:
    """Returns a valid installation access token for `integration`,
    minting + caching a fresh one if the cached one is missing or close
    to expiry. Shares GitHubIntegration.access_token_encrypted with PAT
    mode (different lifetime semantics, same column — see models.py)."""
    if integration.installation_id is None:
        raise GitHubApiError("This integration has no GitHub App installation on file")

    now = datetime.utcnow()
    if (
        integration.access_token_encrypted
        and integration.access_token_expires_at
        and integration.access_token_expires_at - timedelta(seconds=_TOKEN_REFRESH_BUFFER_SECONDS) > now
    ):
        return crypto.decrypt(integration.access_token_encrypted)

    data = await _app_request("POST", f"/app/installations/{integration.installation_id}/access_tokens")
    integration.access_token_encrypted = crypto.encrypt(data["token"])
    # GitHub returns an ISO8601 "...Z" expiry; fromisoformat needs the
    # explicit +00:00 offset instead.
    integration.access_token_expires_at = datetime.fromisoformat(data["expires_at"].replace("Z", "+00:00")).replace(tzinfo=None)
    await db.flush()
    return data["token"]


async def upsert_installation(db: AsyncSession, installation_id: int, account_login: str, created_by: Optional[object]) -> GitHubIntegration:
    """Get-or-create by installation_id — called from both the callback
    (best-effort, UX-only) and the `installation` webhook (the actual
    source of truth, see webhook_api.py), so a callback that never
    arrives still leaves the integration correctly provisioned the first
    time GitHub delivers that webhook."""
    integration = (
        await db.execute(select(GitHubIntegration).where(GitHubIntegration.installation_id == installation_id))
    ).scalar_one_or_none()
    if integration is None:
        integration = GitHubIntegration(
            name=account_login, github_org=account_login, auth_mode=GitHubAuthMode.github_app,
            installation_id=installation_id, status=GitHubIntegrationStatus.connected, created_by=created_by,
        )
        db.add(integration)
    else:
        integration.status = GitHubIntegrationStatus.connected
        integration.last_error = None
    await db.flush()
    return integration


async def register_repositories(db: AsyncSession, integration: GitHubIntegration, repo_payloads: list[dict]) -> list[GitHubRepository]:
    """Get-or-creates GitHubRepository rows from the `installation`/
    `installation_repositories` webhooks' repository shape — id/name/
    full_name/private only, unlike the REST API's full repository object
    (sync.py's add_repository), so owner/html_url are derived from
    full_name and default_branch is left at the model default ("main")
    until a manual/scheduled sync fills in the real value. Reactivates a
    previously-removed repo (see deactivate_repositories) rather than
    creating a duplicate row, keyed on GitHubRepository.external_id's
    unique constraint exactly like sync.py's own get-or-create."""
    result = []
    for repo_payload in repo_payloads:
        existing = (
            await db.execute(select(GitHubRepository).where(GitHubRepository.external_id == repo_payload["id"]))
        ).scalar_one_or_none()
        if existing is not None:
            existing.is_active = True
            result.append(existing)
            continue
        full_name = repo_payload["full_name"]
        repo = GitHubRepository(
            integration_id=integration.id, external_id=repo_payload["id"], full_name=full_name,
            name=repo_payload.get("name") or full_name.rsplit("/", 1)[-1],
            owner=full_name.split("/", 1)[0], html_url=f"https://github.com/{full_name}",
        )
        db.add(repo)
        result.append(repo)
    await db.flush()
    return result


async def deactivate_repositories(db: AsyncSession, repo_payloads: list[dict]) -> None:
    """The `installation_repositories` webhook's "removed" half, and the
    whole-installation-uninstalled case (webhook_api.py). Deactivates
    rather than deletes — GitHubPullRequest/Issue/Release/Commit rows FK
    to the repository, and losing repo access on GitHub doesn't mean HUB
    should discard its own history of that repo's activity."""
    external_ids = [r["id"] for r in repo_payloads]
    if not external_ids:
        return
    rows = (
        await db.execute(select(GitHubRepository).where(GitHubRepository.external_id.in_(external_ids)))
    ).scalars().all()
    for row in rows:
        row.is_active = False
    await db.flush()
