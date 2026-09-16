"""HUB-Expansion.md Phase 1 — OperationalEvent emission and correlation
chaining. Not exhaustive over every emission site (see
app/core/event_types.py for the full catalog); covers one inbound
(machine-to-machine) path, one staff-triggered path, and the
multi-request correlation chain Phase 1's ADR calls out as the concrete
win (request -> approve -> deferred execution sharing one correlation_id)."""
from unittest.mock import AsyncMock, patch

from sqlalchemy import select

from app.models import Deployment, DeploymentStatus, OperationalEvent
from tests.conftest import as_user


async def _make_deployment(db_session, *, slug: str) -> Deployment:
    d = Deployment(
        client_name=f"Client {slug}", slug=slug, registration_token_hash="x", status=DeploymentStatus.active,
        base_url="http://example.invalid", api_key_hash="x", action_key_encrypted="unused",
    )
    db_session.add(d)
    await db_session.flush()
    return d


async def test_heartbeat_ingest_emits_event(client, db_session):
    d = await _make_deployment(db_session, slug="evt-1")
    import hashlib
    raw_key = "raw-api-key"
    d.api_key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    await db_session.flush()

    resp = await client.post(
        "/ingest/heartbeat", headers={"Authorization": f"Bearer {raw_key}"},
        json={"app_version": "1.0.0", "usage": [], "pending_requests": []},
    )
    assert resp.status_code == 200

    events = (await db_session.execute(
        select(OperationalEvent).where(OperationalEvent.deployment_id == d.id)
    )).scalars().all()
    assert [e.event_type for e in events] == ["deployment.heartbeat_received"]
    assert events[0].source == "deployment"
    assert events[0].actor_type == "deployment"


async def test_gated_action_chain_shares_one_correlation_id(client, db_session, admin_user):
    """The concrete correlation-chain win from ADR-001: a renew request's
    "requested" event, the generic "approval.requested" event, and the
    eventual domain "executed"/"failed" event must all share the SAME
    correlation_id, even though they're emitted across three separate
    HTTP requests (request, approve, and — automatically — the completion
    hook fired from within the approve request)."""
    d = await _make_deployment(db_session, slug="evt-2")
    as_user(admin_user)
    create = await client.post(f"/deployments/{d.id}/actions/renew", json={"new_expiry_date": "2027-01-01"})
    instance_id = create.json()["instance_id"]

    tasks = await client.get("/approvals/my-tasks")
    task_id = [t for t in tasks.json() if t["status"] == "pending"][0]["id"]

    with patch("app.approvals.deployment_hooks.deployment_client.renew", new_callable=AsyncMock) as mock_renew:
        mock_renew.return_value = {"ok": True}
        await client.post(f"/approvals/tasks/{task_id}/approve", json={"comment": None, "data": {}})

    events = (await db_session.execute(
        select(OperationalEvent).where(OperationalEvent.deployment_id == d.id).order_by(OperationalEvent.created_at)
    )).scalars().all()
    by_type = {e.event_type: e for e in events}
    assert set(by_type) >= {"deployment.renew_requested", "deployment.renew_executed"}

    correlation_ids = {e.correlation_id for e in events}
    assert len(correlation_ids) == 1, [(e.event_type, e.correlation_id) for e in events]

    # approval.requested/approved aren't deployment-scoped by design (see
    # ADR-001) — fetch them separately by entity instead.
    approval_events = (await db_session.execute(
        select(OperationalEvent).where(OperationalEvent.entity_id == instance_id)
    )).scalars().all()
    assert {e.event_type for e in approval_events} == {"approval.requested", "approval.approved"}
    assert {e.correlation_id for e in approval_events} == correlation_ids


async def test_events_endpoint_filters_by_correlation_id(client, db_session, admin_user):
    d = await _make_deployment(db_session, slug="evt-3")
    as_user(admin_user)
    create = await client.post(f"/deployments/{d.id}/actions/renew", json={"new_expiry_date": "2027-01-01"})
    instance_id = create.json()["instance_id"]

    events_resp = await client.get(f"/deployments/{d.id}/action-executions")
    assert events_resp.status_code == 200

    all_events = await client.get("/events", params={"deployment_id": str(d.id)})
    correlation_id = all_events.json()["items"][0]["correlation_id"]

    filtered = await client.get("/events", params={"correlation_id": correlation_id})
    assert filtered.json()["total"] >= 1
    assert all(e["correlation_id"] == correlation_id for e in filtered.json()["items"])
