"""Webhook payload -> local rows + OperationalEvent rows. Called only
from tasks.py's Celery task (process_webhook_event_task) — the fast HTTP
path (webhook_api.py) only verifies the signature and persists the raw
GitHubWebhookEvent row; everything here runs asynchronously, per
HUB-Expansion.md Phase 3's "return quickly ... process heavier work
asynchronously through Celery".

Every event this module handles ends with exactly the events HUB-
Expansion.md's Phase 3 example list gives — no invented "release.updated"
etc. (see docs/integrations/github.md, "do not build unnecessary event
handlers yet")."""
import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import event_types as et
from app.integrations.cicd.github_actions import GitHubActionsProvider, looks_like_version
from app.integrations.github.models import (
    DeploymentGitHubRepository, GitHubIntegration, GitHubRepository, GitHubWebhookEvent, GitHubWebhookEventStatus,
)
from app.integrations.github.upsert import upsert_commit, upsert_issue, upsert_pull_request, upsert_release
from app.models import DeploymentReleaseSource, OperationalEventStatus
from app.services.events import record_event
from app.services.lineage import record_provider_deployment_event

logger = logging.getLogger(__name__)

# HUB-Expansion.md Phase 5 — stateless, safe to share across calls.
_github_actions_provider = GitHubActionsProvider()


async def _resolve_repository(db: AsyncSession, integration: GitHubIntegration, repo_payload: dict) -> GitHubRepository:
    """Finds the local row for the webhook's `repository` object, or
    creates a minimal one if this is the first time HUB has seen it —
    the point of "webhook-driven sync keeps things reasonably current"
    even for a repo nobody has explicitly added yet."""
    repo = (await db.execute(
        select(GitHubRepository).where(GitHubRepository.external_id == repo_payload["id"])
    )).scalar_one_or_none()
    if repo is not None:
        return repo
    repo = GitHubRepository(
        integration_id=integration.id, external_id=repo_payload["id"], full_name=repo_payload["full_name"],
        name=repo_payload["name"], owner=repo_payload["owner"]["login"],
        default_branch=repo_payload.get("default_branch") or "main", html_url=repo_payload["html_url"],
    )
    db.add(repo)
    await db.flush()
    return repo


async def _deployment_id_for(db: AsyncSession, repository: GitHubRepository) -> Optional[object]:
    """OperationalEvent.deployment_id is only set when this repository
    maps to EXACTLY one deployment right now — see docs/integrations/
    github.md's "deployment_id on GitHub events" for why an ambiguous
    (zero or multiple) mapping leaves it NULL instead of guessing."""
    rows = (await db.execute(
        select(DeploymentGitHubRepository.deployment_id).where(DeploymentGitHubRepository.repository_id == repository.id)
    )).scalars().all()
    return rows[0] if len(rows) == 1 else None


async def process_webhook_event(db: AsyncSession, webhook_event: GitHubWebhookEvent, integration: GitHubIntegration) -> None:
    webhook_event.status = GitHubWebhookEventStatus.processing
    await db.flush()

    try:
        handler = _HANDLERS.get(webhook_event.event_type)
        if handler is not None:
            await handler(db, integration, webhook_event)
        webhook_event.status = GitHubWebhookEventStatus.processed
        webhook_event.processed_at = datetime.utcnow()
    except Exception as e:
        webhook_event.status = GitHubWebhookEventStatus.failed
        webhook_event.error = str(e)[:2000]
        logger.warning("GitHub webhook %s (delivery %s) failed to process", webhook_event.event_type, webhook_event.delivery_id, exc_info=True)
        raise
    finally:
        await db.flush()


async def _handle_issues(db: AsyncSession, integration: GitHubIntegration, webhook_event: GitHubWebhookEvent) -> None:
    payload = webhook_event.payload
    repository = await _resolve_repository(db, integration, payload["repository"])
    issue = await upsert_issue(db, repository, payload["issue"])
    deployment_id = await _deployment_id_for(db, repository)
    action = payload.get("action")
    event_type = et.GITHUB_ISSUE_CREATED if action == "opened" else et.GITHUB_ISSUE_UPDATED
    record_event(
        db, event_type=event_type, source=et.SOURCE_GITHUB, actor_type=et.ACTOR_GITHUB_USER,
        entity_type=et.ENTITY_GITHUB_ISSUE, entity_id=issue.id, deployment_id=deployment_id,
        correlation_id=webhook_event.correlation_id, status=OperationalEventStatus.info,
        metadata={
            "repository": repository.full_name, "number": issue.number, "title": issue.title,
            "action": action, "github_login": (payload.get("sender") or {}).get("login"),
        },
    )


async def _handle_pull_request(db: AsyncSession, integration: GitHubIntegration, webhook_event: GitHubWebhookEvent) -> None:
    payload = webhook_event.payload
    repository = await _resolve_repository(db, integration, payload["repository"])
    pr = await upsert_pull_request(db, repository, payload["pull_request"])
    deployment_id = await _deployment_id_for(db, repository)
    action = payload.get("action")
    if action == "opened":
        event_type = et.GITHUB_PULL_REQUEST_OPENED
    elif action == "closed" and payload["pull_request"].get("merged"):
        event_type = et.GITHUB_PULL_REQUEST_MERGED
    elif action == "closed":
        event_type = et.GITHUB_PULL_REQUEST_CLOSED
    else:
        event_type = et.GITHUB_PULL_REQUEST_UPDATED
    record_event(
        db, event_type=event_type, source=et.SOURCE_GITHUB, actor_type=et.ACTOR_GITHUB_USER,
        entity_type=et.ENTITY_GITHUB_PULL_REQUEST, entity_id=pr.id, deployment_id=deployment_id,
        correlation_id=webhook_event.correlation_id, status=OperationalEventStatus.info,
        metadata={
            "repository": repository.full_name, "number": pr.number, "title": pr.title,
            "action": action, "github_login": (payload.get("sender") or {}).get("login"),
        },
    )


async def _handle_push(db: AsyncSession, integration: GitHubIntegration, webhook_event: GitHubWebhookEvent) -> None:
    payload = webhook_event.payload
    repository = await _resolve_repository(db, integration, payload["repository"])
    commits = payload.get("commits") or []
    last_commit = None
    for commit_data in commits:
        last_commit = await upsert_commit(db, repository, commit_data)
    deployment_id = await _deployment_id_for(db, repository)
    record_event(
        db, event_type=et.GITHUB_COMMIT_PUSHED, source=et.SOURCE_GITHUB, actor_type=et.ACTOR_GITHUB_USER,
        entity_type=et.ENTITY_GITHUB_COMMIT, entity_id=last_commit.id if last_commit else None, deployment_id=deployment_id,
        correlation_id=webhook_event.correlation_id, status=OperationalEventStatus.info,
        metadata={
            "repository": repository.full_name, "ref": payload.get("ref"), "commit_count": len(commits),
            "github_login": (payload.get("pusher") or {}).get("name"),
        },
    )


async def _handle_release(db: AsyncSession, integration: GitHubIntegration, webhook_event: GitHubWebhookEvent) -> None:
    payload = webhook_event.payload
    repository = await _resolve_repository(db, integration, payload["repository"])
    release = await upsert_release(db, repository, payload["release"])
    if payload.get("action") != "published":
        return
    deployment_id = await _deployment_id_for(db, repository)
    record_event(
        db, event_type=et.GITHUB_RELEASE_PUBLISHED, source=et.SOURCE_GITHUB, actor_type=et.ACTOR_GITHUB_USER,
        entity_type=et.ENTITY_GITHUB_RELEASE, entity_id=release.id, deployment_id=deployment_id,
        correlation_id=webhook_event.correlation_id, status=OperationalEventStatus.info,
        metadata={
            "repository": repository.full_name, "tag_name": release.tag_name,
            "github_login": (payload.get("sender") or {}).get("login"),
        },
    )


async def _handle_workflow_run(db: AsyncSession, integration: GitHubIntegration, webhook_event: GitHubWebhookEvent) -> None:
    """HUB-Expansion.md Phase 5 — see app/integrations/cicd/github_actions.py
    for the "is this actually a deploy" heuristic and
    docs/adr/ADR-005-cicd-integration.md for why workflow_run rather than
    GitHub's separate Deployments API. Silently records nothing (this
    webhook still ends up `processed`, just with no side effect) when:
    the run wasn't a successful completion, its name/path doesn't look
    like a deploy workflow, or its repository maps to zero/more-than-one
    Deployment (ambiguous — same rule Phase 3's other handlers already
    apply via _deployment_id_for, but here it means skipping entirely
    rather than just leaving OperationalEvent.deployment_id NULL, since a
    DeploymentRelease row MUST belong to exactly one deployment)."""
    payload = webhook_event.payload
    event = _github_actions_provider.receive_deployment_event(payload)
    if event is None:
        return

    repository = await _resolve_repository(db, integration, payload["repository"])
    deployment_id = await _deployment_id_for(db, repository)
    if deployment_id is None:
        return

    version = event.ref if looks_like_version(event.ref) else None
    await record_provider_deployment_event(
        db, deployment_id=deployment_id, repository_id=repository.id, event=event, version=version,
        source=DeploymentReleaseSource.github_actions, correlation_id=webhook_event.correlation_id,
    )


# event_type (X-GitHub-Event header) -> handler. check_run (job/step-level,
# not deployment-level) is deliberately absent — a webhook event with no
# handler here is still persisted (GitHubWebhookEvent) and marked
# `processed`, just without any domain-row/OperationalEvent side effect.
_HANDLERS = {
    "issues": _handle_issues,
    "pull_request": _handle_pull_request,
    "push": _handle_push,
    "release": _handle_release,
    "workflow_run": _handle_workflow_run,
}
