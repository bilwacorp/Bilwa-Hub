"""GitHub webhook ingestion — signature verification, replay/duplicate
rejection, event persistence, and (called directly rather than through a
real Celery broker — see test_github_client.py's module docstring on why
process_webhook_event_task.delay is mocked instead) the processing logic
a Celery task would run, through to OperationalEvent + deployment
correlation."""
import hashlib
import hmac
import json
from unittest.mock import patch

from sqlalchemy import select

from app.integrations.github.models import (
    DeploymentGitHubRepository, GitHubIntegration, GitHubIssue, GitHubRepository, GitHubWebhookEvent,
)
from app.integrations.github.webhooks import process_webhook_event
from app.models import Deployment, DeploymentStatus, OperationalEvent
from app.services import crypto
from tests.conftest import as_user


async def _make_integration(db_session, *, org: str, secret: str = "whsec_test") -> GitHubIntegration:
    integration = GitHubIntegration(name=org, github_org=org, webhook_secret_encrypted=crypto.encrypt(secret))
    db_session.add(integration)
    await db_session.flush()
    return integration


def _sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


ISSUE_PAYLOAD = {
    "action": "opened",
    "issue": {
        "id": 501, "number": 7, "title": "Feed calc wrong", "state": "open",
        "user": {"login": "octocat"}, "html_url": "https://github.com/acme/repo/issues/7",
        "created_at": "2026-09-16T10:00:00Z", "updated_at": "2026-09-16T10:00:00Z", "closed_at": None,
    },
    "repository": {
        "id": 4242, "full_name": "acme/repo", "name": "repo", "owner": {"login": "acme"},
        "default_branch": "main", "html_url": "https://github.com/acme/repo",
    },
    "sender": {"login": "octocat"},
}


async def test_valid_signature_is_accepted_and_persisted(client, db_session):
    integration = await _make_integration(db_session, org="acme-wh1")
    body = json.dumps(ISSUE_PAYLOAD).encode()
    resp = await client.post(
        f"/github/webhooks/{integration.id}", content=body,
        headers={
            "Content-Type": "application/json", "X-Hub-Signature-256": _sign("whsec_test", body),
            "X-GitHub-Delivery": "d-1", "X-GitHub-Event": "issues",
        },
    )
    assert resp.status_code == 202

    event = (await db_session.execute(select(GitHubWebhookEvent).where(GitHubWebhookEvent.delivery_id == "d-1"))).scalar_one()
    assert event.signature_valid is True
    assert event.event_type == "issues"


async def test_invalid_signature_is_rejected_and_not_persisted(client, db_session):
    integration = await _make_integration(db_session, org="acme-wh2")
    body = json.dumps(ISSUE_PAYLOAD).encode()
    resp = await client.post(
        f"/github/webhooks/{integration.id}", content=body,
        headers={
            "Content-Type": "application/json", "X-Hub-Signature-256": "sha256=" + "0" * 64,
            "X-GitHub-Delivery": "d-2", "X-GitHub-Event": "issues",
        },
    )
    assert resp.status_code == 401
    count = (await db_session.execute(
        select(GitHubWebhookEvent).where(GitHubWebhookEvent.delivery_id == "d-2")
    )).scalar_one_or_none()
    assert count is None

    failure_event = (await db_session.execute(
        select(OperationalEvent).where(OperationalEvent.event_type == "github.webhook_failed")
    )).scalar_one_or_none()
    assert failure_event is not None
    assert failure_event.status.value == "failure"


async def test_missing_signature_header_is_rejected(client, db_session):
    integration = await _make_integration(db_session, org="acme-wh3")
    body = json.dumps(ISSUE_PAYLOAD).encode()
    resp = await client.post(
        f"/github/webhooks/{integration.id}", content=body,
        headers={"Content-Type": "application/json", "X-GitHub-Delivery": "d-3", "X-GitHub-Event": "issues"},
    )
    assert resp.status_code == 401


async def test_wrong_secret_is_rejected(client, db_session):
    integration = await _make_integration(db_session, org="acme-wh4", secret="whsec_correct")
    body = json.dumps(ISSUE_PAYLOAD).encode()
    resp = await client.post(
        f"/github/webhooks/{integration.id}", content=body,
        headers={
            "Content-Type": "application/json", "X-Hub-Signature-256": _sign("whsec_wrong", body),
            "X-GitHub-Delivery": "d-4", "X-GitHub-Event": "issues",
        },
    )
    assert resp.status_code == 401


async def test_duplicate_delivery_id_is_idempotent(client, db_session):
    """The actual mechanism (a unique constraint, not a best-effort
    pre-check) — see docs/integrations/github.md's "Webhooks" section."""
    integration = await _make_integration(db_session, org="acme-wh5")
    body = json.dumps(ISSUE_PAYLOAD).encode()
    headers = {
        "Content-Type": "application/json", "X-Hub-Signature-256": _sign("whsec_test", body),
        "X-GitHub-Delivery": "d-5", "X-GitHub-Event": "issues",
    }
    first = await client.post(f"/github/webhooks/{integration.id}", content=body, headers=headers)
    assert first.status_code == 202

    second = await client.post(f"/github/webhooks/{integration.id}", content=body, headers=headers)
    assert second.status_code == 200
    assert second.json()["status"] == "duplicate_delivery"

    rows = (await db_session.execute(select(GitHubWebhookEvent).where(GitHubWebhookEvent.delivery_id == "d-5"))).scalars().all()
    assert len(rows) == 1


async def test_unknown_integration_id_returns_404_not_500(client):
    import uuid
    resp = await client.post(f"/github/webhooks/{uuid.uuid4()}", content=b"{}", headers={"X-GitHub-Delivery": "d-6"})
    assert resp.status_code == 404


async def test_webhook_route_enqueues_processing_task(client, db_session):
    integration = await _make_integration(db_session, org="acme-wh7")
    body = json.dumps(ISSUE_PAYLOAD).encode()
    with patch("app.integrations.github.webhook_api.process_webhook_event_task.delay") as mock_delay:
        resp = await client.post(
            f"/github/webhooks/{integration.id}", content=body,
            headers={
                "Content-Type": "application/json", "X-Hub-Signature-256": _sign("whsec_test", body),
                "X-GitHub-Delivery": "d-7", "X-GitHub-Event": "issues",
            },
        )
    assert resp.status_code == 202
    mock_delay.assert_called_once()
    (webhook_event_id,), _ = mock_delay.call_args
    assert webhook_event_id == resp.json()["webhook_event_id"]


async def test_issue_opened_creates_domain_row_and_operational_event(db_session):
    """Exercises the actual processing logic a Celery task would run
    (webhooks.process_webhook_event), independent of Celery/broker
    machinery — see this module's docstring."""
    integration = await _make_integration(db_session, org="acme-wh8")
    webhook_event = GitHubWebhookEvent(
        integration_id=integration.id, delivery_id="d-8", event_type="issues",
        payload=ISSUE_PAYLOAD, signature_valid=True,
    )
    db_session.add(webhook_event)
    await db_session.flush()

    await process_webhook_event(db_session, webhook_event, integration)

    assert webhook_event.status.value == "processed"
    repo = (await db_session.execute(select(GitHubRepository).where(GitHubRepository.external_id == 4242))).scalar_one()
    assert repo.full_name == "acme/repo"
    issue = (await db_session.execute(select(GitHubIssue).where(GitHubIssue.repository_id == repo.id))).scalar_one()
    assert issue.number == 7
    assert issue.title == "Feed calc wrong"

    events = (await db_session.execute(
        select(OperationalEvent).where(OperationalEvent.event_type == "github.issue.created")
    )).scalars().all()
    assert len(events) == 1
    assert events[0].correlation_id == webhook_event.correlation_id
    assert events[0].actor_type == "github_user"
    assert events[0].actor_id is None
    assert events[0].event_metadata["github_login"] == "octocat"


async def test_push_event_creates_commit_rows_and_one_pushed_event(db_session):
    integration = await _make_integration(db_session, org="acme-wh9")
    push_payload = {
        "ref": "refs/heads/main",
        "commits": [
            {"id": "a" * 40, "message": "fix: feed calc\n\nlonger body", "author": {"name": "Dev One", "email": "dev@acme.test"}, "url": "https://github.com/acme/repo2/commit/a"},
            {"id": "b" * 40, "message": "chore: bump version", "author": {"name": "Dev One", "email": "dev@acme.test"}, "url": "https://github.com/acme/repo2/commit/b"},
        ],
        "pusher": {"name": "octocat"},
        "repository": {
            "id": 5000, "full_name": "acme/repo2", "name": "repo2", "owner": {"login": "acme"},
            "default_branch": "main", "html_url": "https://github.com/acme/repo2",
        },
    }
    webhook_event = GitHubWebhookEvent(
        integration_id=integration.id, delivery_id="d-9", event_type="push", payload=push_payload, signature_valid=True,
    )
    db_session.add(webhook_event)
    await db_session.flush()

    await process_webhook_event(db_session, webhook_event, integration)

    from app.integrations.github.models import GitHubCommit
    repo = (await db_session.execute(select(GitHubRepository).where(GitHubRepository.external_id == 5000))).scalar_one()
    commits = (await db_session.execute(select(GitHubCommit).where(GitHubCommit.repository_id == repo.id))).scalars().all()
    assert len(commits) == 2

    events = (await db_session.execute(
        select(OperationalEvent).where(OperationalEvent.event_type == "github.commit.pushed")
    )).scalars().all()
    assert len(events) == 1  # one push -> one event, not one per commit
    assert events[0].event_metadata["commit_count"] == 2


async def test_event_gets_deployment_id_only_when_repository_maps_to_exactly_one_deployment(db_session):
    integration = await _make_integration(db_session, org="acme-wh10")
    d1 = Deployment(client_name="D1", slug="wh10-d1", registration_token_hash="x", status=DeploymentStatus.active)
    db_session.add(d1)
    await db_session.flush()

    payload = {**ISSUE_PAYLOAD, "repository": {**ISSUE_PAYLOAD["repository"], "id": 6000, "full_name": "acme/repo3"}}
    webhook_event = GitHubWebhookEvent(
        integration_id=integration.id, delivery_id="d-10", event_type="issues", payload=payload, signature_valid=True,
    )
    db_session.add(webhook_event)
    await db_session.flush()
    await process_webhook_event(db_session, webhook_event, integration)

    repo = (await db_session.execute(select(GitHubRepository).where(GitHubRepository.external_id == 6000))).scalar_one()
    # Not mapped to any deployment yet -> deployment_id stays NULL.
    event = (await db_session.execute(select(OperationalEvent).where(OperationalEvent.event_type == "github.issue.created"))).scalar_one()
    assert event.deployment_id is None

    # Map it to exactly one deployment, then a second issue event should
    # get deployment_id set.
    db_session.add(DeploymentGitHubRepository(deployment_id=d1.id, repository_id=repo.id))
    await db_session.flush()
    payload2 = {**payload, "issue": {**payload["issue"], "id": 502, "number": 8}}
    webhook_event2 = GitHubWebhookEvent(
        integration_id=integration.id, delivery_id="d-11", event_type="issues", payload=payload2, signature_valid=True,
    )
    db_session.add(webhook_event2)
    await db_session.flush()
    await process_webhook_event(db_session, webhook_event2, integration)
    event2 = (await db_session.execute(
        select(OperationalEvent).where(OperationalEvent.event_type == "github.issue.created", OperationalEvent.deployment_id.isnot(None))
    )).scalar_one()
    assert event2.deployment_id == d1.id


async def test_processing_failure_marks_webhook_event_failed_not_silently_dropped(db_session):
    integration = await _make_integration(db_session, org="acme-wh12")
    # Malformed payload (missing required "repository" key) — processing
    # should fail loudly, not silently succeed with half the data.
    webhook_event = GitHubWebhookEvent(
        integration_id=integration.id, delivery_id="d-12", event_type="issues", payload={"action": "opened"}, signature_valid=True,
    )
    db_session.add(webhook_event)
    await db_session.flush()

    import pytest
    with pytest.raises(KeyError):
        await process_webhook_event(db_session, webhook_event, integration)
    assert webhook_event.status.value == "failed"
    assert webhook_event.error is not None
