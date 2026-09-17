"""HUB-Expansion.md Phase 5 — CI/CD webhook integration. Exercises
app/integrations/github/webhooks.py's workflow_run handler (called
through process_webhook_event, same "run the Celery task's logic
directly" pattern as test_github_webhooks.py) and the DeploymentProvider
abstraction it delegates to (app/integrations/cicd/)."""
from sqlalchemy import select

from app.integrations.github.models import (
    DeploymentGitHubRepository, GitHubIntegration, GitHubRelease, GitHubRepository, GitHubWebhookEvent,
)
from app.integrations.github.webhooks import process_webhook_event
from app.models import Deployment, DeploymentRelease, DeploymentStatus


async def _make_integration(db_session, *, org: str) -> GitHubIntegration:
    integration = GitHubIntegration(name=org, github_org=org, access_token_encrypted=None, webhook_secret_encrypted=None)
    db_session.add(integration)
    await db_session.flush()
    return integration


async def _make_deployment(db_session, *, slug: str) -> Deployment:
    d = Deployment(client_name=f"Client {slug}", slug=slug, registration_token_hash="x", status=DeploymentStatus.active)
    db_session.add(d)
    await db_session.flush()
    return d


def _workflow_run_payload(*, repo_id: int, repo_name: str, name: str = "Deploy", conclusion: str = "success", head_branch: str = "main") -> dict:
    return {
        "action": "completed",
        "workflow_run": {
            "name": name, "path": ".github/workflows/deploy.yml", "head_branch": head_branch,
            "head_sha": "83ad92f" + "0" * 33, "run_number": 42, "status": "completed", "conclusion": conclusion,
            "created_at": "2026-09-16T14:40:00Z", "updated_at": "2026-09-16T14:42:00Z",
            "triggering_actor": {"login": "octocat"},
        },
        "repository": {
            "id": repo_id, "full_name": repo_name, "name": repo_name.split("/")[-1], "owner": {"login": repo_name.split("/")[0]},
            "default_branch": "main", "html_url": f"https://github.com/{repo_name}",
        },
        "sender": {"login": "octocat"},
    }


async def _process(db_session, integration, payload: dict, *, delivery_id: str) -> GitHubWebhookEvent:
    webhook_event = GitHubWebhookEvent(
        integration_id=integration.id, delivery_id=delivery_id, event_type="workflow_run", payload=payload, signature_valid=True,
    )
    db_session.add(webhook_event)
    await db_session.flush()
    await process_webhook_event(db_session, webhook_event, integration)
    return webhook_event


async def test_deploy_workflow_success_records_release_for_mapped_deployment(db_session):
    integration = await _make_integration(db_session, org="acme-cicd-1")
    d = await _make_deployment(db_session, slug="cicd-1")
    payload = _workflow_run_payload(repo_id=9001, repo_name="acme/repo-cicd-1")
    webhook_event = GitHubWebhookEvent(
        integration_id=integration.id, delivery_id="cicd-d1", event_type="workflow_run", payload=payload, signature_valid=True,
    )
    db_session.add(webhook_event)
    await db_session.flush()

    repo = (await db_session.execute(select(GitHubRepository).where(GitHubRepository.external_id == 9001))).scalar_one_or_none()
    assert repo is None  # not yet resolved — the handler creates it on first sight

    await process_webhook_event(db_session, webhook_event, integration)
    assert webhook_event.status.value == "processed"

    repo = (await db_session.execute(select(GitHubRepository).where(GitHubRepository.external_id == 9001))).scalar_one()
    db_session.add(DeploymentGitHubRepository(deployment_id=d.id, repository_id=repo.id))
    await db_session.flush()

    # No mapping existed at process time, so nothing should have been
    # recorded for the first delivery — confirm, then redeliver with the
    # mapping in place (a second, different delivery id) and confirm it
    # DOES record once the repo maps to exactly one deployment.
    rows = (await db_session.execute(select(DeploymentRelease).where(DeploymentRelease.deployment_id == d.id))).scalars().all()
    assert rows == []

    webhook_event_2 = await _process(db_session, integration, payload, delivery_id="cicd-d1-again")
    assert webhook_event_2.status.value == "processed"

    rows = (await db_session.execute(select(DeploymentRelease).where(DeploymentRelease.deployment_id == d.id))).scalars().all()
    assert len(rows) == 1
    release = rows[0]
    assert release.source.value == "github_actions"
    assert release.commit_sha == "83ad92f" + "0" * 33
    assert release.deployed_by == "octocat"
    assert release.repository_id == repo.id
    assert release.version is None  # head_branch "main" doesn't look like a version
    assert release.release_id is None


async def test_non_deploy_workflow_name_is_ignored(db_session):
    """A workflow run that isn't recognized as a deploy is rejected by
    the provider before the handler ever touches GitHubRepository — so
    the repo/mapping are created directly here rather than via a prior
    webhook delivery."""
    integration = await _make_integration(db_session, org="acme-cicd-2")
    d = await _make_deployment(db_session, slug="cicd-2")
    repo = GitHubRepository(
        integration_id=integration.id, external_id=9002, full_name="acme/repo-cicd-2", name="repo-cicd-2",
        owner="acme", html_url="https://github.com/acme/repo-cicd-2",
    )
    db_session.add(repo)
    await db_session.flush()
    db_session.add(DeploymentGitHubRepository(deployment_id=d.id, repository_id=repo.id))
    await db_session.flush()

    payload = _workflow_run_payload(repo_id=9002, repo_name="acme/repo-cicd-2", name="Run Tests")
    payload["workflow_run"]["path"] = ".github/workflows/tests.yml"
    webhook_event = await _process(db_session, integration, payload, delivery_id="cicd-d2")
    assert webhook_event.status.value == "processed"

    rows = (await db_session.execute(select(DeploymentRelease))).scalars().all()
    assert rows == []


async def test_failed_workflow_run_is_ignored(db_session):
    """A non-'success' conclusion is rejected by the provider before the
    handler ever touches GitHubRepository — same reasoning as the
    non-deploy-name test above."""
    integration = await _make_integration(db_session, org="acme-cicd-3")
    d = await _make_deployment(db_session, slug="cicd-3")
    repo = GitHubRepository(
        integration_id=integration.id, external_id=9003, full_name="acme/repo-cicd-3", name="repo-cicd-3",
        owner="acme", html_url="https://github.com/acme/repo-cicd-3",
    )
    db_session.add(repo)
    await db_session.flush()
    db_session.add(DeploymentGitHubRepository(deployment_id=d.id, repository_id=repo.id))
    await db_session.flush()

    payload = _workflow_run_payload(repo_id=9003, repo_name="acme/repo-cicd-3", conclusion="failure")
    webhook_event = await _process(db_session, integration, payload, delivery_id="cicd-d3")
    assert webhook_event.status.value == "processed"

    rows = (await db_session.execute(select(DeploymentRelease).where(DeploymentRelease.deployment_id == d.id))).scalars().all()
    assert rows == []


async def test_ambiguous_repository_mapping_skips_recording(db_session):
    integration = await _make_integration(db_session, org="acme-cicd-4")
    d1 = await _make_deployment(db_session, slug="cicd-4a")
    d2 = await _make_deployment(db_session, slug="cicd-4b")
    payload = _workflow_run_payload(repo_id=9004, repo_name="acme/repo-cicd-4")

    pre = await _process(db_session, integration, payload, delivery_id="cicd-d4-pre")
    assert pre.status.value == "processed"
    repo = (await db_session.execute(select(GitHubRepository).where(GitHubRepository.external_id == 9004))).scalar_one()
    db_session.add(DeploymentGitHubRepository(deployment_id=d1.id, repository_id=repo.id))
    db_session.add(DeploymentGitHubRepository(deployment_id=d2.id, repository_id=repo.id))
    await db_session.flush()

    webhook_event = await _process(db_session, integration, payload, delivery_id="cicd-d4")
    assert webhook_event.status.value == "processed"

    rows = (await db_session.execute(select(DeploymentRelease))).scalars().all()
    assert rows == []


async def test_version_like_branch_resolves_matching_github_release(db_session):
    integration = await _make_integration(db_session, org="acme-cicd-5")
    d = await _make_deployment(db_session, slug="cicd-5")
    payload = _workflow_run_payload(repo_id=9005, repo_name="acme/repo-cicd-5", head_branch="2.8.15")

    pre = await _process(db_session, integration, payload, delivery_id="cicd-d5-pre")
    assert pre.status.value == "processed"
    repo = (await db_session.execute(select(GitHubRepository).where(GitHubRepository.external_id == 9005))).scalar_one()
    db_session.add(DeploymentGitHubRepository(deployment_id=d.id, repository_id=repo.id))
    release = GitHubRelease(
        repository_id=repo.id, external_id=1, tag_name="v2.8.15", html_url="https://github.com/acme/repo-cicd-5/releases/v2.8.15",
    )
    db_session.add(release)
    await db_session.flush()

    webhook_event = await _process(db_session, integration, payload, delivery_id="cicd-d5")
    assert webhook_event.status.value == "processed"

    rows = (await db_session.execute(select(DeploymentRelease).where(DeploymentRelease.deployment_id == d.id))).scalars().all()
    assert len(rows) == 1
    assert rows[0].version == "2.8.15"
    assert rows[0].release_id == release.id
