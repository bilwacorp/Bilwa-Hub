"""Row-level visibility — HUB-Expansion.md Phase 20's explicit ask:
"test unauthorized access using IDs belonging to another deployment.
Expected behavior: 404 or equivalent non-disclosing response." Covers the
shared _get_visible_or_404 pattern (deployments.py/tickets.py/
maintenance.py/services/deployment_scope.py)."""
import uuid
from datetime import datetime, timedelta

from app.models import Deployment, DeploymentStaffAssignment, DeploymentStatus, MaintenanceWindow, SupportTicket
from tests.conftest import as_user


async def _make_deployment(db_session, *, slug: str) -> Deployment:
    d = Deployment(client_name=f"Client {slug}", slug=slug, registration_token_hash="x", status=DeploymentStatus.active)
    db_session.add(d)
    await db_session.flush()
    return d


async def test_unassigned_engineer_sees_empty_deployment_list(client, db_session, engineer_user):
    await _make_deployment(db_session, slug="rlv-1")
    as_user(engineer_user)
    resp = await client.get("/deployments")
    assert resp.status_code == 200
    assert resp.json() == {"total": 0, "items": []}


async def test_unassigned_engineer_gets_404_for_known_deployment_id(client, db_session, engineer_user):
    d = await _make_deployment(db_session, slug="rlv-2")
    as_user(engineer_user)
    resp = await client.get(f"/deployments/{d.id}")
    assert resp.status_code == 404


async def test_assigned_engineer_sees_only_their_deployment(client, db_session, engineer_user):
    mine = await _make_deployment(db_session, slug="rlv-3")
    other = await _make_deployment(db_session, slug="rlv-4")
    db_session.add(DeploymentStaffAssignment(deployment_id=mine.id, user_id=engineer_user.id))
    await db_session.flush()

    as_user(engineer_user)
    resp = await client.get("/deployments")
    assert resp.status_code == 200
    assert [item["id"] for item in resp.json()["items"]] == [str(mine.id)]

    assert (await client.get(f"/deployments/{mine.id}")).status_code == 200
    assert (await client.get(f"/deployments/{other.id}")).status_code == 404


async def test_admin_view_all_sees_every_deployment(client, db_session, admin_user):
    await _make_deployment(db_session, slug="rlv-5")
    await _make_deployment(db_session, slug="rlv-6")
    as_user(admin_user)
    resp = await client.get("/deployments")
    assert resp.json()["total"] == 2


async def test_ticket_visibility_follows_its_deployment_assignment(client, db_session, engineer_user):
    mine = await _make_deployment(db_session, slug="rlv-7")
    other = await _make_deployment(db_session, slug="rlv-8")
    db_session.add(DeploymentStaffAssignment(deployment_id=mine.id, user_id=engineer_user.id))
    my_ticket = SupportTicket(deployment_id=mine.id, subject="s", description="d", priority="normal")
    other_ticket = SupportTicket(deployment_id=other.id, subject="s2", description="d2", priority="normal")
    db_session.add_all([my_ticket, other_ticket])
    await db_session.flush()

    as_user(engineer_user)
    resp = await client.get("/tickets")
    assert [t["id"] for t in resp.json()["items"]] == [str(my_ticket.id)]
    assert (await client.get(f"/tickets/{other_ticket.id}")).status_code == 404
    assert (await client.get(f"/tickets/{my_ticket.id}")).status_code == 200


async def test_maintenance_fleet_wide_window_always_visible_but_deployment_one_is_scoped(client, db_session, engineer_user):
    """maintenance.py's explicit exception to the row-level pattern:
    deployment_id IS NULL windows are never scoped, since they're not
    about any one deployment (core/permissions.py)."""
    mine = await _make_deployment(db_session, slug="rlv-9")
    other = await _make_deployment(db_session, slug="rlv-10")
    db_session.add(DeploymentStaffAssignment(deployment_id=mine.id, user_id=engineer_user.id))
    start = datetime.utcnow() + timedelta(days=1)
    fleet_wide = MaintenanceWindow(deployment_id=None, scheduled_start=start, scheduled_end=start + timedelta(hours=1), description="fleet")
    mine_window = MaintenanceWindow(deployment_id=mine.id, scheduled_start=start, scheduled_end=start + timedelta(hours=1), description="mine")
    other_window = MaintenanceWindow(deployment_id=other.id, scheduled_start=start, scheduled_end=start + timedelta(hours=1), description="other")
    db_session.add_all([fleet_wide, mine_window, other_window])
    await db_session.flush()

    as_user(engineer_user)
    resp = await client.get("/maintenance-windows")
    visible_ids = {w["id"] for w in resp.json()["items"]}
    assert visible_ids == {str(fleet_wide.id), str(mine_window.id)}


async def test_nonexistent_deployment_id_also_404s(client, admin_user):
    """A caller who CAN see everything still gets a plain 404 for an id
    that simply doesn't exist — not a 500 or a different shape."""
    as_user(admin_user)
    resp = await client.get(f"/deployments/{uuid.uuid4()}")
    assert resp.status_code == 404
