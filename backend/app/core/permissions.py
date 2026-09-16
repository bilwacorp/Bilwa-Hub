"""Permissions — a Casbin `(resource, action)` pair per gated feature area,
enforced via require_permission() below. `casbin_rule` (p/g rows) is the
enforcement source of truth; `Role`/`Permission` (app/models.py) are DB-backed
*metadata* layered on top purely for the admin-facing Roles & Permissions UI
(api/routers/rbac.py) — a Permission row doesn't grant anything by itself,
it just makes a (resource, action) pair visible/toggleable in that UI. This
mirrors PoultryPro-CBF's core/permissions.py (same split), scaled down to
this hub's much smaller resource set.

Was originally two permissions (STAFF_MANAGE, and one coarse FLEET_MANAGE
covering deployments/tickets/maintenance/notifications together) with no
catalog or custom-role support — migration 010_rbac_catalog split
FLEET_MANAGE into one permission per resource (behavior-preserving: 'admin'
and 'engineer' were re-seeded with the exact same *effective* access they
already had, just as separate rows now) so a custom role can be scoped to
e.g. just tickets+notifications without also granting deployment control.

ALL_PERMISSIONS is the canonical catalog — new permissions are added here
*and* seeded into the `permissions` table by a migration, never invented at
runtime (see rbac.py's list_permissions, which just reads that table)."""
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
STAFF_MANAGE = ("staff", "manage")                # users.py
DEPLOYMENTS_MANAGE = ("deployments", "manage")     # deployments.py (deployment registry + inbound actions + staff assignment)
TICKETS_MANAGE = ("tickets", "manage")             # tickets.py
MAINTENANCE_MANAGE = ("maintenance", "manage")     # maintenance.py
NOTIFICATIONS_MANAGE = ("notifications", "manage") # notifications.py
RBAC_MANAGE = ("rbac", "manage")                   # rbac.py — the roles/permissions catalog itself

# (resource, action, description) — the migration-seeded catalog. Order here
# is also the order the admin UI renders the permission grid in.
ALL_PERMISSIONS: list[tuple[str, str, str]] = [
    (*STAFF_MANAGE, "Create, deactivate, and reset the password of staff accounts; assign roles"),
    (*DEPLOYMENTS_MANAGE, "Register client deployments, run renew/suspend/change-plan/extend-expiry actions, review subscription requests, assign staff to a deployment"),
    (*TICKETS_MANAGE, "View and update the status of support tickets relayed from client deployments"),
    (*MAINTENANCE_MANAGE, "Create and edit fleet maintenance windows"),
    (*NOTIFICATIONS_MANAGE, "View notification history, resend/delete entries, send test emails/WhatsApp messages"),
    (*RBAC_MANAGE, "Create/delete custom roles and change which permissions any role holds"),
]


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
