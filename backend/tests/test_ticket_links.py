"""HUB-Expansion.md Phase 6 — Support <-> Engineering link. Covers
SupportTicketLink CRUD + permission/row-scoping, and the ticket timeline
aggregation (services/ticket_links.ticket_timeline)."""
import uuid
from datetime import datetime

from app.core import event_types as et
from app.integrations.github.models import GitHubIntegration, GitHubIssue, GitHubPullRequest, GitHubRepository
from app.models import Deployment, DeploymentStaffAssignment, DeploymentStatus, SupportTicket
from app.services.events import record_event
from tests.conftest import as_user, make_user


async def _make_deployment(db_session, *, slug: str) -> Deployment:
    d = Deployment(client_name=f"Client {slug}", slug=slug, registration_token_hash="x", status=DeploymentStatus.active)
    db_session.add(d)
    await db_session.flush()
    return d


async def _make_ticket(db_session, deployment: Deployment, *, subject: str = "Feed calc wrong") -> SupportTicket:
    t = SupportTicket(deployment_id=deployment.id, subject=subject, description="d", priority="normal")
    db_session.add(t)
    await db_session.flush()
    return t


async def _make_repo_with_issue_and_pr(db_session, *, org: str):
    integration = GitHubIntegration(name=org, github_org=org, access_token_encrypted=None, webhook_secret_encrypted=None)
    db_session.add(integration)
    await db_session.flush()
    repo = GitHubRepository(
        integration_id=integration.id, external_id=1, full_name=f"{org}/repo", name="repo", owner=org,
        html_url=f"https://github.com/{org}/repo",
    )
    db_session.add(repo)
    await db_session.flush()
    issue = GitHubIssue(
        repository_id=repo.id, external_id=1, number=829, title="Feed calc wrong", state="open",
        html_url=f"https://github.com/{org}/repo/issues/829",
        opened_at=datetime(2026, 9, 16, 10, 0, 0), github_updated_at=datetime(2026, 9, 16, 10, 0, 0),
    )
    pr = GitHubPullRequest(
        repository_id=repo.id, external_id=2, number=841, title="Fix feed calc", state="open", is_draft=False,
        html_url=f"https://github.com/{org}/repo/pull/841",
        opened_at=datetime(2026, 9, 16, 11, 0, 0), github_updated_at=datetime(2026, 9, 16, 11, 0, 0),
    )
    db_session.add_all([issue, pr])
    await db_session.flush()
    return repo, issue, pr


async def test_create_and_list_github_issue_link(client, db_session, admin_user):
    d = await _make_deployment(db_session, slug="tl-1")
    t = await _make_ticket(db_session, d)
    _, issue, _ = await _make_repo_with_issue_and_pr(db_session, org="acme-tl1")

    as_user(admin_user)
    resp = await client.post(f"/tickets/{t.id}/links", json={"link_type": "github_issue", "target_id": str(issue.id)})
    assert resp.status_code == 201
    body = resp.json()
    assert body["label"] == "GitHub #829: Feed calc wrong"
    assert body["url"] == issue.html_url
    assert body["target_status"] == "open"

    listing = await client.get(f"/tickets/{t.id}/links")
    assert listing.status_code == 200
    assert len(listing.json()) == 1


async def test_unknown_target_id_is_rejected(client, db_session, admin_user):
    d = await _make_deployment(db_session, slug="tl-2")
    t = await _make_ticket(db_session, d)
    as_user(admin_user)
    resp = await client.post(f"/tickets/{t.id}/links", json={"link_type": "github_issue", "target_id": str(uuid.uuid4())})
    assert resp.status_code == 400


async def test_duplicate_link_is_rejected(client, db_session, admin_user):
    d = await _make_deployment(db_session, slug="tl-3")
    t = await _make_ticket(db_session, d)
    _, issue, _ = await _make_repo_with_issue_and_pr(db_session, org="acme-tl3")
    as_user(admin_user)
    first = await client.post(f"/tickets/{t.id}/links", json={"link_type": "github_issue", "target_id": str(issue.id)})
    assert first.status_code == 201
    second = await client.post(f"/tickets/{t.id}/links", json={"link_type": "github_issue", "target_id": str(issue.id)})
    assert second.status_code == 400


async def test_delete_link(client, db_session, admin_user):
    d = await _make_deployment(db_session, slug="tl-4")
    t = await _make_ticket(db_session, d)
    _, issue, _ = await _make_repo_with_issue_and_pr(db_session, org="acme-tl4")
    as_user(admin_user)
    created = await client.post(f"/tickets/{t.id}/links", json={"link_type": "github_issue", "target_id": str(issue.id)})
    link_id = created.json()["id"]

    resp = await client.delete(f"/tickets/{t.id}/links/{link_id}")
    assert resp.status_code == 204

    listing = await client.get(f"/tickets/{t.id}/links")
    assert listing.json() == []


async def test_no_role_user_cannot_manage_links_but_engineer_can(client, db_session, no_role_user):
    d = await _make_deployment(db_session, slug="tl-5")
    t = await _make_ticket(db_session, d)
    _, issue, _ = await _make_repo_with_issue_and_pr(db_session, org="acme-tl5")

    as_user(no_role_user)
    resp = await client.post(f"/tickets/{t.id}/links", json={"link_type": "github_issue", "target_id": str(issue.id)})
    assert resp.status_code == 403

    engineer = await make_user(db_session, role="engineer", username="eng-tl5")
    db_session.add(DeploymentStaffAssignment(deployment_id=d.id, user_id=engineer.id))
    await db_session.flush()
    as_user(engineer)
    resp = await client.post(f"/tickets/{t.id}/links", json={"link_type": "github_issue", "target_id": str(issue.id)})
    assert resp.status_code == 201


async def test_link_endpoints_404_for_ticket_outside_scope(client, db_session):
    d = await _make_deployment(db_session, slug="tl-6")
    t = await _make_ticket(db_session, d)
    scoped_engineer = await make_user(db_session, role="engineer", username="eng-scoped-tl6")
    as_user(scoped_engineer)  # not assigned to d
    resp = await client.get(f"/tickets/{t.id}/links")
    assert resp.status_code == 404


async def test_timeline_aggregates_ticket_deployment_and_linked_issue_only(client, db_session, admin_user):
    d = await _make_deployment(db_session, slug="tl-7")
    t = await _make_ticket(db_session, d)
    _, issue, pr = await _make_repo_with_issue_and_pr(db_session, org="acme-tl7")

    # Ticket-created, deployment-heartbeat, and issue-created events —
    # all pre-existing OperationalEvent emission sites, not anything new.
    record_event(
        db_session, event_type=et.TICKET_CREATED, source=et.SOURCE_DEPLOYMENT, actor_type=et.ACTOR_DEPLOYMENT,
        entity_type=et.ENTITY_TICKET, entity_id=t.id, deployment_id=d.id,
    )
    record_event(
        db_session, event_type=et.DEPLOYMENT_HEARTBEAT_RECEIVED, source=et.SOURCE_DEPLOYMENT, actor_type=et.ACTOR_DEPLOYMENT,
        entity_type=et.ENTITY_DEPLOYMENT, entity_id=d.id, deployment_id=d.id,
    )
    record_event(
        db_session, event_type=et.GITHUB_ISSUE_CREATED, source=et.SOURCE_GITHUB, actor_type=et.ACTOR_GITHUB_USER,
        entity_type=et.ENTITY_GITHUB_ISSUE, entity_id=issue.id,
    )
    # A PR event that exists but is NEVER linked to this ticket — must not
    # leak into its timeline.
    record_event(
        db_session, event_type=et.GITHUB_PULL_REQUEST_OPENED, source=et.SOURCE_GITHUB, actor_type=et.ACTOR_GITHUB_USER,
        entity_type=et.ENTITY_GITHUB_PULL_REQUEST, entity_id=pr.id,
    )
    await db_session.flush()

    as_user(admin_user)
    await client.post(f"/tickets/{t.id}/links", json={"link_type": "github_issue", "target_id": str(issue.id)})

    resp = await client.get(f"/tickets/{t.id}/timeline")
    assert resp.status_code == 200
    event_types = [e["event_type"] for e in resp.json()]
    assert event_types == ["ticket.created", "deployment.heartbeat_received", "github.issue.created", "ticket.link_added"]


async def test_timeline_includes_linked_pr_once_linked(client, db_session, admin_user):
    d = await _make_deployment(db_session, slug="tl-8")
    t = await _make_ticket(db_session, d)
    _, _, pr = await _make_repo_with_issue_and_pr(db_session, org="acme-tl8")
    record_event(
        db_session, event_type=et.GITHUB_PULL_REQUEST_MERGED, source=et.SOURCE_GITHUB, actor_type=et.ACTOR_GITHUB_USER,
        entity_type=et.ENTITY_GITHUB_PULL_REQUEST, entity_id=pr.id,
    )
    await db_session.flush()

    as_user(admin_user)
    before = await client.get(f"/tickets/{t.id}/timeline")
    assert before.json() == []

    await client.post(f"/tickets/{t.id}/links", json={"link_type": "github_pull_request", "target_id": str(pr.id)})
    after = await client.get(f"/tickets/{t.id}/timeline")
    event_types = [e["event_type"] for e in after.json()]
    assert "github.pull_request.merged" in event_types
