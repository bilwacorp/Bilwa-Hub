"""Permissions — Phase 1 shipped with no catalog table or roles UI, a
single STAFF_MANAGE permission gating every staff route. This is the first
real per-resource split: STAFF_MANAGE now covers only staff/user
management (admin-only), and FLEET_MANAGE covers deployments/tickets/
maintenance (granted to both 'admin' and 'engineer' — see
alembic/versions/005_staff_roles_and_fleet_permission.py and
services/rbac.py). require_permission()'s shape is ported from
PoultryOS-CBP's core/permissions.py so a future phase can keep growing a
real per-resource catalog without changing how routes are gated."""
from fastapi import Depends, HTTPException, status

from app.core.casbin_enforcer import get_enforcer
from app.core.deps import get_current_user
from app.models import User

DEFAULT_DOMAIN = "default"

# register.py / ingest.py / the hub_integration equivalent on the
# deployment side are NOT Casbin-gated at all — they're machine-to-machine,
# authenticated by a shared secret instead (api/routers/ingest.py verifies
# the deployment's api_key_hash directly against the Deployment row, no
# Casbin involved).
STAFF_MANAGE = ("staff", "manage")  # users.py — admin only
FLEET_MANAGE = ("fleet", "manage")  # deployments/tickets/maintenance — admin + engineer


async def has_permission(user_id: str, resource: str, action: str) -> bool:
    # enforce() itself is sync — it only reads the already-loaded in-memory
    # policy; only load_policy()/save_policy() touch the DB and need await.
    return get_enforcer().enforce(user_id, DEFAULT_DOMAIN, resource, action)


def require_permission(resource: str, action: str):
    async def _dep(current_user: User = Depends(get_current_user)) -> User:
        if not await has_permission(str(current_user.id), resource, action):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not permitted")
        return current_user
    return _dep
