"""HUB-Expansion.md Phase 10 — Operations Dashboard. Covers the fleet
health bucketing, a couple of the documented proxy definitions
(services/dashboard.py), row-level scoping, and the permission gate."""
from datetime import datetime, timedelta

from app.models import (
    Deployment, DeploymentSnapshot, DeploymentStaffAssignment, DeploymentStatus, SupportTicket,
)
from tests.conftest import as_user, make_user


async def _make_deployment(db_session, *, slug: str) -> Deployment:
    d = Deployment(client_name=f"Client {slug}", slug=slug, registration_token_hash="x", status=DeploymentStatus.active)
    db_session.add(d)
    await db_session.flush()
    return d


async def _heartbeat(db_session, deployment: Deployment, *, hours_ago: float) -> None:
    db_session.add(DeploymentSnapshot(
        deployment_id=deployment.id, app_version="1.0.0", received_at=datetime.utcnow() - timedelta(hours=hours_ago),
    ))
    await db_session.flush()


async def test_no_role_user_is_forbidden(client, no_role_user):
    as_user(no_role_user)
    resp = await client.get("/dashboard")
    assert resp.status_code == 403


async def test_fleet_buckets_by_heartbeat_age(client, db_session, admin_user):
    healthy = await _make_deployment(db_session, slug="dash-1")
    await _heartbeat(db_session, healthy, hours_ago=1)
    stale = await _make_deployment(db_session, slug="dash-2")
    await _heartbeat(db_session, stale, hours_ago=5)
    offline = await _make_deployment(db_session, slug="dash-3")
    await _heartbeat(db_session, offline, hours_ago=10)
    never = await _make_deployment(db_session, slug="dash-4")

    as_user(admin_user)
    resp = await client.get("/dashboard")
    assert resp.status_code == 200
    fleet = resp.json()["fleet"]
    assert fleet["healthy"] >= 1
    assert fleet["warning"] >= 1
    assert fleet["offline"] >= 1
    assert fleet["unknown"] >= 1


async def test_escalated_ticket_counted_and_unassigned_flagged(client, db_session, admin_user):
    d = await _make_deployment(db_session, slug="dash-5")
    db_session.add(SupportTicket(deployment_id=d.id, subject="Urgent issue", description="d", priority="high"))
    await db_session.flush()

    as_user(admin_user)
    resp = await client.get("/dashboard")
    support = resp.json()["support"]
    assert support["escalated"] >= 1
    assert support["unassigned"] >= 1

    kinds = [a["kind"] for a in resp.json()["attention"]]
    assert "ticket_unassigned" in kinds


async def test_engineer_only_sees_assigned_deployments(client, db_session):
    assigned = await _make_deployment(db_session, slug="dash-6a")
    await _heartbeat(db_session, assigned, hours_ago=1)
    unassigned = await _make_deployment(db_session, slug="dash-6b")
    await _heartbeat(db_session, unassigned, hours_ago=1)

    engineer = await make_user(db_session, role="engineer", username="eng-dash6")
    db_session.add(DeploymentStaffAssignment(deployment_id=assigned.id, user_id=engineer.id))
    await db_session.flush()

    as_user(engineer)
    resp = await client.get("/dashboard")
    assert resp.status_code == 200
    assert resp.json()["fleet"]["total"] == 1
