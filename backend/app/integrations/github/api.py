"""Casbin-gated GitHub management/browsing endpoints — see
docs/integrations/github.md's "Authorization" section for why this
surface is not row-scoped (unlike deployments/tickets/maintenance). The
public webhook endpoint lives in webhook_api.py, mirroring the existing
register.py/ingest.py-vs-deployments.py separation of "machine-
authenticated" from "Casbin-authenticated" routes."""
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import GITHUB_MANAGE, GITHUB_SYNC, GITHUB_TEST_CONNECTION, GITHUB_VIEW, require_permission
from app.db.session import get_db
from app.integrations.github import client, sync
from app.integrations.github.client import GitHubApiError
from app.integrations.github.models import (
    DeploymentGitHubRepository, GitHubIntegration, GitHubIntegrationStatus, GitHubIssue, GitHubPullRequest,
    GitHubRelease, GitHubRepository,
)
from app.integrations.github.schemas import (
    GitHubIntegrationCreate, GitHubIntegrationOut, GitHubIntegrationUpdate, GitHubIssueOut, GitHubPullRequestOut,
    GitHubReleaseOut, GitHubRepositoryAddRequest, GitHubRepositoryDeploymentMappingsUpdate, GitHubRepositoryOut,
    GitHubTestConnectionResult,
)
from app.integrations.github.tasks import sync_repository_task
from app.models import User
from app.services import crypto

router = APIRouter(prefix="/github", tags=["github"])


def _integration_out(integration: GitHubIntegration) -> GitHubIntegrationOut:
    """Explicit field-by-field construction, never
    GitHubIntegrationOut.model_validate(integration) — that would read
    access_token_encrypted/webhook_secret_encrypted onto a response
    schema field if one ever got added there by accident. Building the
    has_access_token/has_webhook_secret booleans by hand is the point:
    there is no code path here that can leak the encrypted value, let
    alone the decrypted one."""
    return GitHubIntegrationOut(
        id=integration.id, name=integration.name, github_org=integration.github_org,
        auth_mode=integration.auth_mode, status=integration.status,
        has_access_token=bool(integration.access_token_encrypted),
        has_webhook_secret=bool(integration.webhook_secret_encrypted),
        last_synced_at=integration.last_synced_at, last_webhook_at=integration.last_webhook_at,
        last_error=integration.last_error, last_error_at=integration.last_error_at,
        created_at=integration.created_at, webhook_url_path=f"/api/v1/github/webhooks/{integration.id}",
    )


async def _get_integration_or_404(db: AsyncSession, integration_id: str) -> GitHubIntegration:
    integration = await db.get(GitHubIntegration, integration_id)
    if integration is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "GitHub integration not found")
    return integration


async def _get_repository_or_404(db: AsyncSession, repository_id: str) -> GitHubRepository:
    repo = await db.get(GitHubRepository, repository_id)
    if repo is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "GitHub repository not found")
    return repo


@router.post("/integrations", response_model=GitHubIntegrationOut, status_code=201)
async def create_integration(
    body: GitHubIntegrationCreate, db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(*GITHUB_MANAGE)),
):
    integration = GitHubIntegration(
        name=body.name, github_org=body.github_org,
        access_token_encrypted=crypto.encrypt(body.access_token),
        webhook_secret_encrypted=crypto.encrypt(body.webhook_secret),
        created_by=current_user.id,
    )
    db.add(integration)
    await db.flush()
    return _integration_out(integration)


@router.get("/integrations", response_model=list[GitHubIntegrationOut])
async def list_integrations(db: AsyncSession = Depends(get_db), current_user: User = Depends(require_permission(*GITHUB_VIEW))):
    rows = (await db.execute(select(GitHubIntegration).order_by(GitHubIntegration.created_at.desc()))).scalars().all()
    return [_integration_out(i) for i in rows]


@router.patch("/integrations/{integration_id}", response_model=GitHubIntegrationOut)
async def update_integration(
    integration_id: str, body: GitHubIntegrationUpdate, db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(*GITHUB_MANAGE)),
):
    integration = await _get_integration_or_404(db, integration_id)
    if body.name is not None:
        integration.name = body.name
    if body.access_token is not None:
        integration.access_token_encrypted = crypto.encrypt(body.access_token)
    if body.webhook_secret is not None:
        integration.webhook_secret_encrypted = crypto.encrypt(body.webhook_secret)
    await db.flush()
    return _integration_out(integration)


@router.post("/integrations/{integration_id}/test-connection", response_model=GitHubTestConnectionResult)
async def test_connection(
    integration_id: str, db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(*GITHUB_TEST_CONNECTION)),
):
    integration = await _get_integration_or_404(db, integration_id)
    try:
        data = await client.test_connection(integration)
    except GitHubApiError as e:
        integration.status = GitHubIntegrationStatus.error
        integration.last_error = str(e)[:2000]
        integration.last_error_at = datetime.utcnow()
        await db.flush()
        return GitHubTestConnectionResult(ok=False, detail=str(e))
    integration.status = GitHubIntegrationStatus.connected
    integration.last_error = None
    await db.flush()
    return GitHubTestConnectionResult(ok=True, detail="Connected", github_login=data.get("login"))


@router.post("/integrations/{integration_id}/repositories", response_model=GitHubRepositoryOut, status_code=201)
async def add_repository(
    integration_id: str, body: GitHubRepositoryAddRequest, db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(*GITHUB_MANAGE)),
):
    integration = await _get_integration_or_404(db, integration_id)
    try:
        repo = await sync.add_repository(db, integration, body.full_name)
    except GitHubApiError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e))
    return GitHubRepositoryOut.model_validate(repo)


@router.get("/repositories", response_model=list[GitHubRepositoryOut])
async def list_repositories(
    integration_id: Optional[uuid.UUID] = Query(None), db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(*GITHUB_VIEW)),
):
    conditions = []
    if integration_id is not None:
        conditions.append(GitHubRepository.integration_id == integration_id)
    rows = (await db.execute(select(GitHubRepository).where(*conditions).order_by(GitHubRepository.full_name))).scalars().all()
    return [GitHubRepositoryOut.model_validate(r) for r in rows]


@router.put("/repositories/{repository_id}/deployments", status_code=204)
async def set_repository_deployment_mappings(
    repository_id: str, body: GitHubRepositoryDeploymentMappingsUpdate, db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(*GITHUB_MANAGE)),
):
    """Replaces this repository's whole deployment-mapping set wholesale
    — same "replace, don't incrementally patch" shape as deployments.py's
    PUT .../staff."""
    repo = await _get_repository_or_404(db, repository_id)
    await db.execute(delete(DeploymentGitHubRepository).where(DeploymentGitHubRepository.repository_id == repo.id))
    for mapping in body.mappings:
        db.add(DeploymentGitHubRepository(deployment_id=mapping.deployment_id, repository_id=repo.id, is_primary=mapping.is_primary))
    await db.flush()


@router.post("/repositories/{repository_id}/sync", status_code=202)
async def sync_repository(
    repository_id: str, db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(*GITHUB_SYNC)),
):
    repo = await _get_repository_or_404(db, repository_id)
    sync_repository_task.delay(str(repo.id))
    return {"status": "sync_queued"}


@router.get("/pull-requests", response_model=list[GitHubPullRequestOut])
async def list_pull_requests(
    repository_id: Optional[uuid.UUID] = Query(None), db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(*GITHUB_VIEW)),
):
    conditions = []
    if repository_id is not None:
        conditions.append(GitHubPullRequest.repository_id == repository_id)
    rows = (await db.execute(
        select(GitHubPullRequest).where(*conditions).order_by(GitHubPullRequest.opened_at.desc()).limit(200)
    )).scalars().all()
    return [GitHubPullRequestOut.model_validate(r) for r in rows]


@router.get("/issues", response_model=list[GitHubIssueOut])
async def list_issues(
    repository_id: Optional[uuid.UUID] = Query(None), db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(*GITHUB_VIEW)),
):
    conditions = []
    if repository_id is not None:
        conditions.append(GitHubIssue.repository_id == repository_id)
    rows = (await db.execute(
        select(GitHubIssue).where(*conditions).order_by(GitHubIssue.opened_at.desc()).limit(200)
    )).scalars().all()
    return [GitHubIssueOut.model_validate(r) for r in rows]


@router.get("/releases", response_model=list[GitHubReleaseOut])
async def list_releases(
    repository_id: Optional[uuid.UUID] = Query(None), db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(*GITHUB_VIEW)),
):
    conditions = []
    if repository_id is not None:
        conditions.append(GitHubRelease.repository_id == repository_id)
    rows = (await db.execute(
        select(GitHubRelease).where(*conditions).order_by(GitHubRelease.created_at.desc()).limit(200)
    )).scalars().all()
    return [GitHubReleaseOut.model_validate(r) for r in rows]
