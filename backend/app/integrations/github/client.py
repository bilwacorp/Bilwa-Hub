"""httpx wrapper for HUB -> GitHub REST API calls — the one place a
GitHub access token is decrypted and used. Mirrors
services/deployment_client.py's shape deliberately (same _call/typed-
exception pattern) for consistency: DeploymentCallError there,
GitHubApiError/GitHubRateLimitError here.

Never paginates beyond the first page (100 items) of any list endpoint —
correct for a repo with a normal amount of history; a known limitation
for a very old/active one (see docs/integrations/github.md)."""
from typing import Any, Optional

import httpx

from app.integrations.github.models import GitHubIntegration
from app.services import crypto

_API_BASE = "https://api.github.com"
_HTTP_TIMEOUT = 15.0
_PER_PAGE = 100


class GitHubApiError(Exception):
    """A GitHub API call failed. Base class also used directly for a
    permanent failure (404 unknown repo, 422, bad credentials) — tasks.py
    deliberately does NOT autoretry on the base class, only on the two
    subclasses below, both genuinely transient."""


class GitHubRateLimitError(GitHubApiError):
    """403 with x-ratelimit-remaining: 0, or a plain 429 — will succeed
    again after retry_after_seconds."""
    def __init__(self, message: str, retry_after_seconds: Optional[int] = None):
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class GitHubConnectionError(GitHubApiError):
    """DNS/timeout/connection-refused — a transport-level failure, not a
    GitHub-side rejection. Worth retrying; a 404/422 is not."""


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


async def _request(integration: GitHubIntegration, method: str, path: str, *, params: Optional[dict] = None) -> Any:
    if not integration.access_token_encrypted:
        raise GitHubApiError("This integration has no access token on file")
    token = crypto.decrypt(integration.access_token_encrypted)

    try:
        async with httpx.AsyncClient(base_url=_API_BASE, timeout=_HTTP_TIMEOUT) as client:
            resp = await client.request(method, path, headers=_headers(token), params=params)
    except httpx.HTTPError as e:
        raise GitHubConnectionError(f"{method} {path} -> connection failed: {e}") from e

    if resp.status_code == 429:
        retry_after = resp.headers.get("retry-after")
        raise GitHubRateLimitError(f"{method} {path} -> rate limited (429)", retry_after_seconds=int(retry_after) if retry_after else 60)
    if resp.status_code == 403 and resp.headers.get("x-ratelimit-remaining") == "0":
        reset = resp.headers.get("x-ratelimit-reset")
        raise GitHubRateLimitError(f"{method} {path} -> primary rate limit exhausted", retry_after_seconds=60 if not reset else None)
    if resp.status_code >= 400:
        raise GitHubApiError(f"{method} {path} -> {resp.status_code}: {resp.text[:300]}")
    return resp.json()


async def test_connection(integration: GitHubIntegration) -> dict:
    """GET /user — the cheapest call that both confirms the token works
    and identifies who it belongs to, without assuming org-membership
    scopes the token might not have."""
    return await _request(integration, "GET", "/user")


async def get_repository(integration: GitHubIntegration, full_name: str) -> dict:
    return await _request(integration, "GET", f"/repos/{full_name}")


async def list_pull_requests(integration: GitHubIntegration, full_name: str) -> list[dict]:
    result = await _request(integration, "GET", f"/repos/{full_name}/pulls", params={"state": "all", "per_page": _PER_PAGE})
    return result or []


async def list_issues(integration: GitHubIntegration, full_name: str) -> list[dict]:
    # GitHub's /issues endpoint also returns pull requests (a PR is an
    # issue in their model) — filtered out here so GitHubIssue rows never
    # duplicate what GitHubPullRequest already owns.
    result = await _request(integration, "GET", f"/repos/{full_name}/issues", params={"state": "all", "per_page": _PER_PAGE})
    return [item for item in (result or []) if "pull_request" not in item]


async def list_releases(integration: GitHubIntegration, full_name: str) -> list[dict]:
    result = await _request(integration, "GET", f"/repos/{full_name}/releases", params={"per_page": _PER_PAGE})
    return result or []
