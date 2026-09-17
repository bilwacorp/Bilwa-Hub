"""HUB-Expansion.md Phase 4 helpers — batch lookups for the lineage fields
on DeploymentOut (same "_assigned_staff_map" shape api/routers/
deployments.py already uses for staff, rather than a relationship()
lazy-loaded under AsyncSession) plus infer_release_from_heartbeat(), the
one piece of automatic DeploymentRelease population this phase ships
(everything else is a manual entry via POST /deployments/{id}/releases).
"""
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import event_types as et
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
    if repo_ids:
        match = (await db.execute(
            select(GitHubRelease).where(
                GitHubRelease.repository_id.in_(repo_ids),
                GitHubRelease.tag_name.in_([app_version, f"v{app_version}"]),
            ).order_by(GitHubRelease.published_at.desc()).limit(1)
        )).scalar_one_or_none()
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
