"""Permission enforcement — HUB-Expansion.md Phase 20's "permission tests".
Not exhaustive over every one of the ~48 (resource, action) pairs in
core/permissions.py; a small representative sample per gating shape
(require_permission on a plain endpoint, and the has_permission +
row-level-visibility combination) is the point, not full coverage."""
from tests.conftest import as_user


async def test_list_deployments_requires_permission(client, no_role_user):
    as_user(no_role_user)
    resp = await client.get("/deployments")
    assert resp.status_code == 403


async def test_list_deployments_allowed_with_permission(client, admin_user):
    as_user(admin_user)
    resp = await client.get("/deployments")
    assert resp.status_code == 200
    assert resp.json() == {"total": 0, "items": []}


async def test_engineer_lacks_admin_only_permissions(client, engineer_user):
    """migration 011's whole point: engineer keeps action permissions but
    not view_all/admin-config ones — rbac.manage is admin-only."""
    as_user(engineer_user)
    resp = await client.get("/rbac/roles")
    assert resp.status_code == 403


async def test_admin_has_rbac_manage(client, admin_user):
    as_user(admin_user)
    resp = await client.get("/rbac/roles")
    assert resp.status_code == 200


async def test_unauthenticated_request_is_rejected(client):
    resp = await client.get("/deployments")
    assert resp.status_code == 401
