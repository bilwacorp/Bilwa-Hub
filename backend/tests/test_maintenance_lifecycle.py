"""HUB-Expansion.md Phase 7/8 — maintenance lifecycle + its approval gate.
migration 018 seeds an active, published `maintenance_window_approval`
workflow routed to 'admin', so a high-risk (fleet-wide or lockout) window
is gated by default — same "gated by default" precedent as
test_deployment_action_execution.py's renew/suspend/change_plan."""
from datetime import datetime
from unittest.mock import AsyncMock, patch

from sqlalchemy import select

from app.core.maintenance_scheduler import _auto_transition
from app.models import Deployment, DeploymentStatus, MaintenanceWindow, MaintenanceWindowStatus, OperationalEvent
from tests.conftest import as_user


async def _make_deployment(db_session, *, slug: str, registered: bool = False) -> Deployment:
    d = Deployment(client_name=f"Client {slug}", slug=slug, registration_token_hash="x", status=DeploymentStatus.active)
    if registered:
        d.base_url = "http://example.invalid"
        d.api_key_hash = "x"
        d.action_key_encrypted = "unused-in-these-tests"
    db_session.add(d)
    await db_session.flush()
    return d


async def _get_task_id(client) -> str:
    resp = await client.get("/approvals/my-tasks")
    tasks = [t for t in resp.json() if t["status"] == "pending"]
    assert len(tasks) == 1, tasks
    return tasks[0]["id"]


async def test_plain_single_deployment_window_is_not_gated(client, db_session, admin_user):
    """The common case (pre-Phase-7 behavior): a single-deployment,
    banner-mode window goes straight to planned, no approval involved."""
    d = await _make_deployment(db_session, slug="maint-1")
    as_user(admin_user)
    resp = await client.post("/maintenance-windows", json={
        "deployment_id": str(d.id), "scheduled_start": "2027-01-01T00:00:00Z", "scheduled_end": "2027-01-01T02:00:00Z",
        "description": "Routine upgrade", "mode": "banner",
    })
    assert resp.status_code == 201
    assert resp.json()["status"] == "planned"


async def test_save_as_draft_creates_a_draft_with_no_push_or_approval(client, db_session, admin_user):
    d = await _make_deployment(db_session, slug="maint-2")
    as_user(admin_user)
    resp = await client.post("/maintenance-windows", json={
        "deployment_id": str(d.id), "scheduled_start": "2027-01-01T00:00:00Z", "scheduled_end": "2027-01-01T02:00:00Z",
        "description": "Draft plan", "save_as_draft": True,
    })
    assert resp.status_code == 201
    assert resp.json()["status"] == "draft"


async def test_submit_draft_promotes_a_low_risk_window_to_planned(client, db_session, admin_user):
    d = await _make_deployment(db_session, slug="maint-3")
    as_user(admin_user)
    created = await client.post("/maintenance-windows", json={
        "deployment_id": str(d.id), "scheduled_start": "2027-01-01T00:00:00Z", "scheduled_end": "2027-01-01T02:00:00Z",
        "description": "Draft plan", "save_as_draft": True,
    })
    window_id = created.json()["id"]

    submitted = await client.post(f"/maintenance-windows/{window_id}/submit")
    assert submitted.status_code == 200
    assert submitted.json()["status"] == "planned"


async def test_submit_twice_is_rejected(client, db_session, admin_user):
    d = await _make_deployment(db_session, slug="maint-3b")
    as_user(admin_user)
    created = await client.post("/maintenance-windows", json={
        "deployment_id": str(d.id), "scheduled_start": "2027-01-01T00:00:00Z", "scheduled_end": "2027-01-01T02:00:00Z",
        "description": "Draft plan", "save_as_draft": True,
    })
    window_id = created.json()["id"]
    await client.post(f"/maintenance-windows/{window_id}/submit")
    second = await client.post(f"/maintenance-windows/{window_id}/submit")
    assert second.status_code == 409


async def test_lockout_mode_window_requires_approval(client, db_session, admin_user):
    d = await _make_deployment(db_session, slug="maint-4")
    as_user(admin_user)
    resp = await client.post("/maintenance-windows", json={
        "deployment_id": str(d.id), "scheduled_start": "2027-01-01T00:00:00Z", "scheduled_end": "2027-01-01T02:00:00Z",
        "description": "Emergency lockdown", "mode": "lockout",
    })
    assert resp.status_code == 201
    assert resp.json()["status"] == "approval_required"

    task_id = await _get_task_id(client)
    approve = await client.post(f"/approvals/tasks/{task_id}/approve", json={"comment": None, "data": {}})
    assert approve.status_code == 200

    window_id = resp.json()["id"]
    detail = await db_session.get(MaintenanceWindow, window_id)
    assert detail.status == MaintenanceWindowStatus.approved
    assert detail.approved_by == admin_user.id


async def test_fleet_wide_window_requires_approval(client, db_session, admin_user):
    as_user(admin_user)
    resp = await client.post("/maintenance-windows", json={
        "deployment_id": None, "scheduled_start": "2027-01-01T00:00:00Z", "scheduled_end": "2027-01-01T02:00:00Z",
        "description": "Fleet-wide DB upgrade", "mode": "read_only",
    })
    assert resp.status_code == 201
    assert resp.json()["status"] == "approval_required"


async def test_rejected_approval_sends_window_back_to_draft(client, db_session, admin_user):
    d = await _make_deployment(db_session, slug="maint-5")
    as_user(admin_user)
    created = await client.post("/maintenance-windows", json={
        "deployment_id": str(d.id), "scheduled_start": "2027-01-01T00:00:00Z", "scheduled_end": "2027-01-01T02:00:00Z",
        "description": "Risky change", "mode": "lockout",
    })
    window_id = created.json()["id"]
    task_id = await _get_task_id(client)
    reject = await client.post(f"/approvals/tasks/{task_id}/reject", json={"comment": "not now", "data": {}})
    assert reject.status_code == 200

    window = await db_session.get(MaintenanceWindow, window_id)
    assert window.status == MaintenanceWindowStatus.draft


async def test_approved_window_is_promoted_to_planned_by_next_scheduler_tick(client, db_session, admin_user):
    d = await _make_deployment(db_session, slug="maint-6")
    as_user(admin_user)
    created = await client.post("/maintenance-windows", json={
        "deployment_id": str(d.id), "scheduled_start": "2027-01-01T00:00:00Z", "scheduled_end": "2027-01-01T02:00:00Z",
        "description": "Risky change", "mode": "lockout",
    })
    window_id = created.json()["id"]
    task_id = await _get_task_id(client)
    await client.post(f"/approvals/tasks/{task_id}/approve", json={"comment": None, "data": {}})

    window = await db_session.get(MaintenanceWindow, window_id)
    assert window.status == MaintenanceWindowStatus.approved

    await _auto_transition(db_session)
    await db_session.refresh(window)
    assert window.status == MaintenanceWindowStatus.planned


async def test_manual_status_update_to_completed_and_failed_emit_specific_events(client, db_session, admin_user):
    d = await _make_deployment(db_session, slug="maint-7")
    as_user(admin_user)
    created = await client.post("/maintenance-windows", json={
        "deployment_id": str(d.id), "scheduled_start": "2027-01-01T00:00:00Z", "scheduled_end": "2027-01-01T02:00:00Z",
        "description": "Routine upgrade",
    })
    window_id = created.json()["id"]

    completed = await client.patch(f"/maintenance-windows/{window_id}", json={"status": "completed", "actual_impact": "No downtime observed"})
    assert completed.status_code == 200
    assert completed.json()["actual_impact"] == "No downtime observed"

    events = (await db_session.execute(
        select(OperationalEvent).where(OperationalEvent.event_type == "maintenance.completed")
    )).scalars().all()
    assert len(events) == 1
    assert events[0].status.value == "success"


async def test_scheduler_auto_transitions_planned_to_in_progress_and_emits_event(db_session):
    d = await _make_deployment(db_session, slug="maint-8", registered=True)
    window = MaintenanceWindow(
        deployment_id=d.id, scheduled_start=datetime(2020, 1, 1), scheduled_end=datetime(2099, 1, 1),
        description="Long window", status=MaintenanceWindowStatus.planned,
    )
    db_session.add(window)
    await db_session.flush()

    with patch("app.services.deployment_client.push_maintenance", new_callable=AsyncMock):
        await _auto_transition(db_session)
    await db_session.refresh(window)
    assert window.status == MaintenanceWindowStatus.in_progress

    events = (await db_session.execute(
        select(OperationalEvent).where(OperationalEvent.event_type == "maintenance.started")
    )).scalars().all()
    assert len(events) == 1
    assert events[0].deployment_id == d.id
