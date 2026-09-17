"""HUB-Expansion.md Phase 4 helpers — batch lookups for the lineage fields
on DeploymentOut (same "_assigned_staff_map" shape api/routers/
deployments.py already uses for staff, rather than a relationship()
lazy-loaded under AsyncSession) plus infer_release_from_heartbeat(), the
one piece of automatic DeploymentRelease population Phase 4 shipped.
Phase 5 (HUB-Expansion.md, CI/CD webhook integration) adds a second
source: record_provider_deployment_event(), fed by
app/integrations/cicd/'s DeploymentProvider implementations via
app/integrations/github/webhooks.py's workflow_run handler."""
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import event_types as et
from app.integrations.cicd.provider import DeploymentEventData
from app.integrations.github.models import DeploymentGitHubRepository, GitHubRelease
from app.models import Application, Customer, Deployment, DeploymentRelease, DeploymentReleaseSource
from app.services.events import record_event


async def customers_map(db: AsyncSession, customer_ids: list) -> dict:
    ids = [i for i in customer_ids if i is not None]
    if not ids:
        return {}
    rows = (await db.execute(select(Customer).where(Customer.id.in_(ids)))).scalars().all()
    return {c.id: c for c in rows}


async def applications_map(db: AsyncSession, application_ids: list) -> dict:
    ids = [i for i in application_ids if i is not None]
    if not ids:
        return {}
    rows = (await db.execute(select(Application).where(Application.id.in_(ids)))).scalars().all()
    return {a.id: a for a in rows}


async def current_releases_map(db: AsyncSession, deployment_ids: list) -> dict:
    """Most recent DeploymentRelease row (by deployed_at) per deployment_id
    — one query for however many deployments are being rendered, same
    batching shape as api/routers/deployments.py's _assigned_staff_map."""
    if not deployment_ids:
        return {}
    rows = (await db.execute(
        select(DeploymentRelease).where(DeploymentRelease.deployment_id.in_(deployment_ids))
        .order_by(DeploymentRelease.deployment_id, DeploymentRelease.deployed_at.desc())
    )).scalars().all()
    out: dict = {}
    for r in rows:
        out.setdefault(r.deployment_id, r)  # first row per id wins (deployed_at desc)
    return out


async def _match_release_by_tag(db: AsyncSession, repo_ids: list, version: str) -> Optional[GitHubRelease]:
    """Shared by infer_release_from_heartbeat (below) and
    record_provider_deployment_event — matches a version string against
    a GitHubRelease tag ('2.8.15' or 'v2.8.15') on any of the given
    repos, most recently published first."""
    if not repo_ids:
        return None
    return (await db.execute(
        select(GitHubRelease).where(
            GitHubRelease.repository_id.in_(repo_ids), GitHubRelease.tag_name.in_([version, f"v{version}"]),
        ).order_by(GitHubRelease.published_at.desc()).limit(1)
    )).scalar_one_or_none()


async def infer_release_from_heartbeat(
    db: AsyncSession, deployment: Deployment, app_version: Optional[str], received_at: datetime,
) -> None:
    """Called from ingest.py after every heartbeat. Records a new
    DeploymentRelease row only when app_version is present AND differs
    from the most recently recorded version for this deployment — a
    heartbeat that reports the same version as last time is a no-op here,
    so this doesn't grow one row per heartbeat (that's already
    DeploymentSnapshot's job). Matches app_version against a GitHubRelease
    tag ('2.8.15' or 'v2.8.15') on one of this deployment's linked repos
    (Phase 3's DeploymentGitHubRepository) — no match still records the
    version-change itself (source stays 'heartbeat_inferred', repository_id
    /release_id/commit_sha stay NULL), since the version change is real
    lineage even without a resolved GitHub release."""
    if not app_version:
        return

    latest = (await db.execute(
        select(DeploymentRelease).where(DeploymentRelease.deployment_id == deployment.id)
        .order_by(DeploymentRelease.deployed_at.desc()).limit(1)
    )).scalar_one_or_none()
    if latest is not None and latest.version == app_version:
        return

    repo_ids = (await db.execute(
        select(DeploymentGitHubRepository.repository_id).where(DeploymentGitHubRepository.deployment_id == deployment.id)
    )).scalars().all()

    repository_id = release_id = commit_sha = None
    match = await _match_release_by_tag(db, repo_ids, app_version)
    if match is not None:
        repository_id, release_id, commit_sha = match.repository_id, match.id, match.target_commit_sha

    release = DeploymentRelease(
        id=uuid.uuid4(), deployment_id=deployment.id, version=app_version,
        repository_id=repository_id, release_id=release_id, commit_sha=commit_sha,
        source=DeploymentReleaseSource.heartbeat_inferred, deployed_by=None, deployed_at=received_at,
    )
    db.add(release)
    await db.flush()
    record_event(
        db, event_type=et.DEPLOYMENT_RELEASE_RECORDED, source=et.SOURCE_DEPLOYMENT, actor_type=et.ACTOR_DEPLOYMENT,
        actor_id=deployment.id, entity_type=et.ENTITY_DEPLOYMENT_RELEASE, entity_id=release.id, deployment_id=deployment.id,
        metadata={"version": app_version, "source": "heartbeat_inferred", "matched_release": release_id is not None},
    )


async def record_provider_deployment_event(
    db: AsyncSession, *, deployment_id: uuid.UUID, repository_id: uuid.UUID, event: DeploymentEventData,
    version: Optional[str], source: DeploymentReleaseSource, correlation_id: Optional[uuid.UUID] = None,
) -> DeploymentRelease:
    """HUB-Expansion.md Phase 5 — the CI/CD counterpart to
    infer_release_from_heartbeat: called from app/integrations/github/
    webhooks.py's workflow_run handler with a DeploymentEventData already
    parsed by a DeploymentProvider (app/integrations/cicd/). `version` is
    resolved by the caller (provider-specific heuristic — see
    app/integrations/cicd/github_actions.py's looks_like_version), not
    here, so this function stays provider-agnostic. Unlike the heartbeat
    path, every CI/CD event is recorded unconditionally (no "same version
    as last time" dedup) — a webhook delivery is itself already
    idempotent (GitHubWebhookEvent.delivery_id's unique constraint), and
    each one is a genuinely new deploy occurrence even if it happens to
    redeploy the same version."""
    release_id = None
    if version:
        match = await _match_release_by_tag(db, [repository_id], version)
        if match is not None:
            release_id = match.id

    release = DeploymentRelease(
        deployment_id=deployment_id, version=version, repository_id=repository_id, release_id=release_id,
        commit_sha=event.commit_sha, source=source, deployed_by=event.deployed_by, deployed_at=event.deployed_at,
        notes=event.description,
    )
    db.add(release)
    await db.flush()
    record_event(
        db, event_type=et.DEPLOYMENT_RELEASE_RECORDED, source=et.SOURCE_GITHUB, actor_type=et.ACTOR_GITHUB_USER,
        entity_type=et.ENTITY_DEPLOYMENT_RELEASE, entity_id=release.id, deployment_id=deployment_id,
        correlation_id=correlation_id, metadata={"version": version, "source": source.value, "commit_sha": event.commit_sha},
    )
    return release
