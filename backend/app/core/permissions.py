"""Permissions — Phase 1 deliberately has no catalog table or roles UI (see
the plan's "Casbin on day one" section): every staff route requires the
same single STAFF_MANAGE permission, granted to the one seeded 'admin' role
(alembic/versions/002_seed_admin.py). require_permission()'s shape is
ported from PoultryOS-CBP's core/permissions.py so a future phase can grow
a real per-resource catalog without changing how routes are gated."""
from fastapi import Depends, HTTPException, status

from app.core.casbin_enforcer import get_enforcer
from app.core.deps import get_current_user
from app.models import User

DEFAULT_DOMAIN = "default"

# Every staff-facing route in Phase 1 (deployments, tickets, maintenance)
# gates on this one permission. register.py / ingest.py / the hub_integration
# equivalent on the deployment side are NOT Casbin-gated at all — they're
# machine-to-machine, authenticated by a shared secret instead (see
# core/deps.py's verify_deployment_action_key... actually that check lives
# on the DEPLOYMENT side; here, api/routers/ingest.py verifies the
# deployment's api_key_hash directly against the Deployment row, no Casbin
# involved).
STAFF_MANAGE = ("staff", "manage")


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
