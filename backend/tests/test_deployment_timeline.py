"""HUB-Expansion.md Phase 9 — unified per-deployment timeline. A thin
GET wrapper over OperationalEvent.deployment_id — no new aggregation
mechanism, see api/routers/deployments.py's get_deployment_timeline."""
from app.core import event_types as et
from app.models import Deployment, DeploymentStaffAssignment, DeploymentStatus
from app.services.events import record_event
from tests.conftest import as_user, make_user


async def _make_deployment(db_session, *, slug: str) -> Deployment:
    d = Deployment(client_name=f"Client {slug}", slug=slug, registration_token_hash="x", status=DeploymentStatus.active)
    db_session.add(d)
    await db_session.flush()
    return d


async def test_timeline_only_returns_this_deployments_events_newest_first(client, db_session, admin_user):
    d1 = await _make_deployment(db_session, slug="tl-1")
    d2 = await _make_deployment(db_session, slug="tl-2")
    record_event(
        db_session, event_type=et.DEPLOYMENT_HEARTBEAT_RECEIVED, source=et.SOURCE_DEPLOYMENT, actor_type=et.ACTOR_DEPLOYMENT,
        entity_type=et.ENTITY_DEPLOYMENT, entity_id=d1.id, deployment_id=d1.id,
    )
    record_event(
        db_session, event_type=et.DEPLOYMENT_HEALTH_CHECK_COMPLETED, source=et.SOURCE_HUB, actor_type=et.ACTOR_STAFF,
        entity_type=et.ENTITY_DEPLOYMENT, entity_id=d1.id, deployment_id=d1.id,
    )
    record_event(
        db_session, event_type=et.DEPLOYMENT_HEARTBEAT_RECEIVED, source=et.SOURCE_DEPLOYMENT, actor_type=et.ACTOR_DEPLOYMENT,
        entity_type=et.ENTITY_DEPLOYMENT, entity_id=d2.id, deployment_id=d2.id,
    )
    await db_session.flush()

    as_user(admin_user)
    resp = await client.get(f"/deployments/{d1.id}/timeline")
    assert resp.status_code == 200
    event_types = [e["event_type"] for e in resp.json()]
    assert event_types == ["deployment.health_check_completed", "deployment.heartbeat_received"]


async def test_timeline_404s_for_deployment_outside_scope(client, db_session):
    d = await _make_deployment(db_session, slug="tl-3")
    scoped_engineer = await make_user(db_session, role="engineer", username="eng-tl3")
    as_user(scoped_engineer)
    resp = await client.get(f"/deployments/{d.id}/timeline")
    assert resp.status_code == 404


async def test_timeline_visible_to_assigned_engineer(client, db_session):
    d = await _make_deployment(db_session, slug="tl-4")
    engineer = await make_user(db_session, role="engineer", username="eng-tl4")
    db_session.add(DeploymentStaffAssignment(deployment_id=d.id, user_id=engineer.id))
    record_event(
        db_session, event_type=et.DEPLOYMENT_HEARTBEAT_RECEIVED, source=et.SOURCE_DEPLOYMENT, actor_type=et.ACTOR_DEPLOYMENT,
        entity_type=et.ENTITY_DEPLOYMENT, entity_id=d.id, deployment_id=d.id,
    )
    await db_session.flush()
    as_user(engineer)
    resp = await client.get(f"/deployments/{d.id}/timeline")
    assert resp.status_code == 200
    assert len(resp.json()) == 1
