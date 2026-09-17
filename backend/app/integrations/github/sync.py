"""Manual/initial GitHub sync — see docs/integrations/github.md's
"Synchronization" section. Called from api.py's sync endpoint (via
tasks.py's Celery task, never inline in the request) and directly by
tests. Raises client.GitHubApiError/GitHubRateLimitError on failure —
callers update GitHubIntegration.status/last_error on that path (see
tasks.py), so a partial failure never leaves rows committed under a
"success" status."""
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.github import client
from app.integrations.github.models import GitHubIntegration, GitHubIntegrationStatus, GitHubRepository
from app.integrations.github.upsert import upsert_issue, upsert_pull_request, upsert_release


async def add_repository(db: AsyncSession, integration: GitHubIntegration, full_name: str) -> GitHubRepository:
    """Fetches the repo from GitHub once to confirm it exists and the
    integration's token can see it, then creates (or returns the
    existing) local row — a no-op if this repo is already known."""
    data = await client.get_repository(db, integration, full_name)
    existing = (await db.execute(select(GitHubRepository).where(GitHubRepository.external_id == data["id"]))).scalar_one_or_none()
    if existing is not None:
        return existing
    repo = GitHubRepository(
        integration_id=integration.id, external_id=data["id"], full_name=data["full_name"],
        name=data["name"], owner=data["owner"]["login"], default_branch=data.get("default_branch") or "main",
        html_url=data["html_url"],
    )
    db.add(repo)
    await db.flush()
    return repo


async def sync_repository(db: AsyncSession, repository: GitHubRepository, integration: GitHubIntegration) -> None:
    repo_data = await client.get_repository(db, integration, repository.full_name)
    repository.default_branch = repo_data.get("default_branch") or repository.default_branch
    repository.html_url = repo_data["html_url"]

    for pr_data in await client.list_pull_requests(db, integration, repository.full_name):
        await upsert_pull_request(db, repository, pr_data)
    for issue_data in await client.list_issues(db, integration, repository.full_name):
        await upsert_issue(db, repository, issue_data)
    for release_data in await client.list_releases(db, integration, repository.full_name):
        await upsert_release(db, repository, release_data)

    repository.last_synced_at = datetime.utcnow()
    integration.status = GitHubIntegrationStatus.connected
    integration.last_synced_at = datetime.utcnow()
    integration.last_error = None
    await db.flush()
