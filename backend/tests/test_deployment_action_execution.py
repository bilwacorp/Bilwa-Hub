"""HUB-Expansion.md Phase 12/13 — the execution state machine and
idempotency guarantees added in app/approvals/deployment_hooks.py.
migration 012 seeds an active, published `deployment_renew` workflow
routed to the 'admin' Casbin role, so a renew request against a fresh
deployment is gated by default — no extra setup needed to exercise the
approval path.

deployment_client.renew is monkeypatched rather than pointed at a real
unreachable URL: deterministic and fast, and it lets us assert the exact
idempotency_key each call receives."""
from unittest.mock import AsyncMock, patch

from app.models import Deployment, DeploymentActionExecution, DeploymentStatus
from tests.conftest import as_user, make_user


async def _make_registered_deployment(db_session, *, slug: str) -> Deployment:
    d = Deployment(
        client_name=f"Client {slug}", slug=slug, registration_token_hash="x", status=DeploymentStatus.active,
        base_url="http://example.invalid", api_key_hash="x", action_key_encrypted="unused-in-these-tests",
    )
    db_session.add(d)
    await db_session.flush()
    return d


async def _get_task_id(client) -> str:
    resp = await client.get("/approvals/my-tasks")
    tasks = [t for t in resp.json() if t["status"] == "pending"]
    assert len(tasks) == 1, tasks
    return tasks[0]["id"]


async def test_renew_request_creates_pending_execution(client, db_session, admin_user):
    d = await _make_registered_deployment(db_session, slug="exec-1")
    as_user(admin_user)
    resp = await client.post(f"/deployments/{d.id}/actions/renew", json={"new_expiry_date": "2027-01-01"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["approval_required"] is True
    assert body["execution_status"] == "pending"

    execution = await db_session.get(DeploymentActionExecution, body["execution_id"])
    assert execution.status.value == "pending"
    assert execution.idempotency_key == f"hub-action-{body['instance_id']}"
    assert execution.attempt_count == 0


async def test_approval_failure_surfaces_as_failed_execution_not_silently(client, db_session, admin_user):
    """The exact bug HUB-Expansion.md Phase 12 describes: approving the
    task must not make the deferred call's own failure invisible."""
    d = await _make_registered_deployment(db_session, slug="exec-2")
    as_user(admin_user)
    create = await client.post(f"/deployments/{d.id}/actions/renew", json={"new_expiry_date": "2027-01-01"})
    execution_id = create.json()["execution_id"]
    task_id = await _get_task_id(client)

    with patch("app.approvals.deployment_hooks.deployment_client.renew", new_callable=AsyncMock) as mock_renew:
        from app.services.deployment_client import DeploymentCallError
        mock_renew.side_effect = DeploymentCallError("connection failed")
        approve = await client.post(f"/approvals/tasks/{task_id}/approve", json={"comment": None, "data": {}})

    # The approval itself still reports success...
    assert approve.status_code == 200
    assert approve.json()["status"] == "completed"

    # ...but the execution row is the source of truth for whether the
    # deployment actually got the call, and it must say it failed.
    execution = await db_session.get(DeploymentActionExecution, execution_id)
    assert execution.status.value == "failed"
    assert execution.attempt_count == 1
    assert "connection failed" in execution.last_error

    listing = await client.get(f"/deployments/{d.id}/action-executions")
    assert listing.json()[0]["status"] == "failed"


async def test_retry_is_idempotent_key_and_increments_attempts(client, db_session, admin_user):
    d = await _make_registered_deployment(db_session, slug="exec-3")
    as_user(admin_user)
    create = await client.post(f"/deployments/{d.id}/actions/renew", json={"new_expiry_date": "2027-01-01"})
    execution_id = create.json()["execution_id"]
    task_id = await _get_task_id(client)

    from app.services.deployment_client import DeploymentCallError

    with patch("app.approvals.deployment_hooks.deployment_client.renew", new_callable=AsyncMock) as mock_renew:
        mock_renew.side_effect = DeploymentCallError("first failure")
        await client.post(f"/approvals/tasks/{task_id}/approve", json={"comment": None, "data": {}})
    first_call_kwargs = mock_renew.call_args.kwargs
    idempotency_key = first_call_kwargs["idempotency_key"]
    assert idempotency_key == f"hub-action-{create.json()['instance_id']}"

    # Retry succeeds this time — same idempotency_key must be sent again.
    with patch("app.approvals.deployment_hooks.deployment_client.renew", new_callable=AsyncMock) as mock_renew_2:
        mock_renew_2.return_value = {"ok": True}
        retry = await client.post(f"/deployments/{d.id}/action-executions/{execution_id}/retry")
    assert retry.status_code == 200
    assert retry.json()["status"] == "executed"
    assert retry.json()["attempt_count"] == 2
    assert mock_renew_2.call_args.kwargs["idempotency_key"] == idempotency_key
    assert len(retry.json()["attempts"]) == 2
    assert [a["status"] for a in retry.json()["attempts"]] == ["failure", "success"]


async def test_cannot_retry_an_already_executed_action(client, db_session, admin_user):
    """HUB-Expansion.md Phase 13: "never blindly repeat money-moving
    actions" — retrying a successfully-executed action must be rejected,
    not silently re-run (which would call deployment_client.renew twice
    for one approval)."""
    d = await _make_registered_deployment(db_session, slug="exec-4")
    as_user(admin_user)
    create = await client.post(f"/deployments/{d.id}/actions/renew", json={"new_expiry_date": "2027-01-01"})
    execution_id = create.json()["execution_id"]
    task_id = await _get_task_id(client)

    with patch("app.approvals.deployment_hooks.deployment_client.renew", new_callable=AsyncMock) as mock_renew:
        mock_renew.return_value = {"ok": True}
        await client.post(f"/approvals/tasks/{task_id}/approve", json={"comment": None, "data": {}})

    retry = await client.post(f"/deployments/{d.id}/action-executions/{execution_id}/retry")
    assert retry.status_code == 409


async def test_cannot_retry_a_still_pending_execution(client, db_session, admin_user):
    """Nothing has been approved yet — there's nothing to retry."""
    d = await _make_registered_deployment(db_session, slug="exec-5")
    as_user(admin_user)
    create = await client.post(f"/deployments/{d.id}/actions/renew", json={"new_expiry_date": "2027-01-01"})
    execution_id = create.json()["execution_id"]

    retry = await client.post(f"/deployments/{d.id}/action-executions/{execution_id}/retry")
    assert retry.status_code == 409


async def test_retry_requires_actions_retry_permission(client, db_session, admin_user):
    """A role that can see/request the action but was never granted
    actions.retry (migration 014) must not be able to retry a failed
    one — actions.retry is deliberately its own permission, not implied
    by deployments.renew (core/permissions.py)."""
    from app.services import rbac

    d = await _make_registered_deployment(db_session, slug="exec-6")
    as_user(admin_user)
    create = await client.post(f"/deployments/{d.id}/actions/renew", json={"new_expiry_date": "2027-01-01"})
    execution_id = create.json()["execution_id"]
    task_id = await _get_task_id(client)
    with patch("app.approvals.deployment_hooks.deployment_client.renew", new_callable=AsyncMock) as mock_renew:
        from app.services.deployment_client import DeploymentCallError
        mock_renew.side_effect = DeploymentCallError("boom")
        await client.post(f"/approvals/tasks/{task_id}/approve", json={"comment": None, "data": {}})

    role = "test_no_retry_role"
    await rbac.set_role_permissions(role, [("deployments", "view"), ("deployments", "view_all"), ("deployments", "renew")])
    limited_user = await make_user(db_session, role=role)

    as_user(limited_user)
    assert (await client.get(f"/deployments/{d.id}/action-executions")).status_code == 200
    retry = await client.post(f"/deployments/{d.id}/action-executions/{execution_id}/retry")
    assert retry.status_code == 403
