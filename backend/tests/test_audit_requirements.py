"""HUB-Expansion.md Phase 19 — Audit Requirements. Before this phase,
neither api/routers/users.py nor api/routers/rbac.py emitted any
OperationalEvent at all (verified by grep before writing this) — these
tests cover the new record_event() calls added there, plus actor_ip
capture (app/core/request_context.py + app/main.py's client_ip_middleware)."""
from sqlalchemy import select

from app.core import event_types as et
from app.models import OperationalEvent, Role
from tests.conftest import as_user


async def test_creating_a_staff_user_is_audited(client, db_session, admin_user):
    as_user(admin_user)
    resp = await client.post("/users", json={
        "username": "new-staffer", "password": "Test@12345", "role": "engineer",
    })
    assert resp.status_code == 201

    event = (await db_session.execute(
        select(OperationalEvent).where(OperationalEvent.event_type == et.STAFF_CREATED)
    )).scalar_one()
    assert event.actor_id == admin_user.id
    assert event.event_metadata["username"] == "new-staffer"
    assert event.event_metadata["role"] == "engineer"


async def test_role_change_and_deactivation_are_audited_with_before_after(client, db_session, admin_user):
    as_user(admin_user)
    created = await client.post("/users", json={"username": "flip-flop", "password": "Test@12345", "role": "engineer"})
    user_id = created.json()["id"]

    role_change = await client.patch(f"/users/{user_id}", json={"role": "admin"})
    assert role_change.status_code == 200
    role_event = (await db_session.execute(
        select(OperationalEvent).where(OperationalEvent.event_type == et.STAFF_ROLE_CHANGED)
    )).scalar_one()
    assert role_event.event_metadata["changes"]["role"] == {"from": "engineer", "to": "admin"}

    deactivate = await client.patch(f"/users/{user_id}", json={"is_active": False})
    assert deactivate.status_code == 200
    deactivated_event = (await db_session.execute(
        select(OperationalEvent).where(OperationalEvent.event_type == et.STAFF_DEACTIVATED)
    )).scalar_one()
    assert deactivated_event.event_metadata["changes"]["is_active"] == {"from": True, "to": False}


async def test_password_reset_is_audited_without_leaking_the_password(client, db_session, admin_user):
    as_user(admin_user)
    created = await client.post("/users", json={"username": "reset-me", "password": "Test@12345", "role": "engineer"})
    user_id = created.json()["id"]

    resp = await client.post(f"/users/{user_id}/reset-password", json={"new_password": "NewPass@2027"})
    assert resp.status_code == 204

    event = (await db_session.execute(
        select(OperationalEvent).where(OperationalEvent.event_type == et.STAFF_PASSWORD_RESET)
    )).scalar_one()
    assert "NewPass@2027" not in str(event.event_metadata)
    assert "password" not in event.event_metadata


async def test_role_lifecycle_is_audited(client, db_session, admin_user):
    as_user(admin_user)
    created = await client.post("/rbac/roles", json={"name": "triage", "description": "Read-mostly"})
    role_id = created.json()["id"]
    (await db_session.execute(select(OperationalEvent).where(OperationalEvent.event_type == et.ROLE_CREATED))).scalar_one()

    renamed = await client.patch(f"/rbac/roles/{role_id}", json={"name": "triage_team"})
    assert renamed.status_code == 200
    rename_event = (await db_session.execute(
        select(OperationalEvent).where(OperationalEvent.event_type == et.ROLE_RENAMED)
    )).scalar_one()
    assert rename_event.event_metadata == {"from_name": "triage", "to_name": "triage_team"}

    perms = await client.put(f"/rbac/roles/{role_id}/permissions", json={"permissions": ["tickets.view"]})
    assert perms.status_code == 200
    perms_event = (await db_session.execute(
        select(OperationalEvent).where(OperationalEvent.event_type == et.ROLE_PERMISSIONS_UPDATED)
    )).scalar_one()
    assert perms_event.event_metadata["new_permissions"] == ["tickets.view"]
    assert perms_event.event_metadata["previous_permissions"] == []

    deleted = await client.delete(f"/rbac/roles/{role_id}")
    assert deleted.status_code == 204
    delete_event = (await db_session.execute(
        select(OperationalEvent).where(OperationalEvent.event_type == et.ROLE_DELETED)
    )).scalar_one()
    assert delete_event.event_metadata["name"] == "triage_team"
    # The role row itself is gone, but its audit trail survives it.
    assert (await db_session.execute(select(Role).where(Role.id == role_id))).scalar_one_or_none() is None


async def test_actor_ip_is_captured_from_x_real_ip_header(client, db_session, admin_user):
    as_user(admin_user)
    resp = await client.post(
        "/users", json={"username": "ip-check", "password": "Test@12345", "role": "engineer"},
        headers={"X-Real-IP": "203.0.113.7"},
    )
    assert resp.status_code == 201
    events = (await db_session.execute(
        select(OperationalEvent).where(OperationalEvent.event_type == et.STAFF_CREATED)
    )).scalars().all()
    event = next(e for e in events if e.event_metadata.get("username") == "ip-check")
    assert event.actor_ip == "203.0.113.7"


async def test_events_router_has_no_write_endpoints(client, admin_user):
    """Audit immutability (HUB-Expansion.md Phase 19: "should not be
    casually editable or deletable") — verified structurally: no route
    exists to PATCH or DELETE an OperationalEvent at all (404, not just
    403 — the path pattern itself doesn't exist for any caller)."""
    as_user(admin_user)
    import uuid
    fake_id = uuid.uuid4()
    assert (await client.patch(f"/events/{fake_id}", json={})).status_code == 404
    assert (await client.delete(f"/events/{fake_id}")).status_code == 404
