"""Split every resource's one coarse '.manage'/'.view' permission into one
permission per action, and add row-level visibility scoping for
deployments/tickets/maintenance — see core/permissions.py's module
docstring for the full design.

NOT a behavior-preserving cutover for 'engineer' — this is deliberate,
per an explicit request: 'engineer' loses `deployments.view_all` /
`tickets.view_all` / `maintenance.view_all`, so after this migration an
engineer only sees deployments explicitly assigned to them (and those
deployments' tickets / deployment-specific maintenance windows) — not the
whole fleet. Fleet-wide maintenance windows (deployment_id IS NULL) stay
visible to anyone holding maintenance.view regardless. 'engineer' keeps
every *action* permission on deployments/tickets/maintenance/notifications
it effectively had before (nothing about what an engineer can *do* to an
in-scope deployment changes, only which deployments are in scope) — it
still does not get staff.* or rbac.* (unchanged from migration 010).
'admin' keeps everything, including both view_all permissions.

Revision ID: 011_granular_permissions
Revises: 010_rbac_catalog
Create Date: 2026-09-19
"""
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '011_granular_permissions'
down_revision: Union[str, None] = '010_rbac_catalog'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DOMAIN = "default"

# The five coarse permissions this migration retires (rbac.manage is not
# in this list — it's kept as-is, just joined by a new rbac.view).
RETIRED = [
    ("staff", "manage"),
    ("deployments", "manage"),
    ("tickets", "manage"),
    ("maintenance", "manage"),
    ("notifications", "manage"),
]

# (resource, action, description) — mirrors core/permissions.py's
# ALL_PERMISSIONS; kept as a literal copy here (not imported) since a
# migration must stay correct even if that module changes shape later.
NEW_PERMISSIONS = [
    ("deployments", "view", "View deployments assigned to you"),
    ("deployments", "view_all", "View every deployment, not just ones assigned to you"),
    ("deployments", "create", "Register a new client deployment and issue its registration token"),
    ("deployments", "renew", "Renew a deployment's subscription"),
    ("deployments", "suspend", "Suspend a deployment's subscription"),
    ("deployments", "change_plan", "Change a deployment's plan"),
    ("deployments", "extend_expiry", "Extend a deployment's subscription expiry date"),
    ("deployments", "check_health", "Run an on-demand live health check against a deployment"),
    ("deployments", "review_request", "Approve or reject a deployment's renewal/upgrade request"),
    ("deployments", "assign_staff", "Assign staff to a deployment"),
    ("tickets", "view", "View support tickets from deployments assigned to you"),
    ("tickets", "view_all", "View support tickets from every deployment"),
    ("tickets", "update_status", "Update a support ticket's status"),
    ("maintenance", "view", "View maintenance windows for deployments assigned to you (fleet-wide windows are always visible)"),
    ("maintenance", "view_all", "View maintenance windows for every deployment"),
    ("maintenance", "create", "Create a maintenance window"),
    ("maintenance", "update", "Edit a maintenance window"),
    ("maintenance", "delete", "Cancel/delete a maintenance window"),
    ("notifications", "view", "View notification history"),
    ("notifications", "resend", "Resend a failed/cancelled notification"),
    ("notifications", "delete", "Delete a notification log entry"),
    ("notifications", "test_send", "Send a test email/WhatsApp message"),
    ("staff", "view", "View staff accounts"),
    ("staff", "create", "Create a staff account"),
    ("staff", "update", "Edit, deactivate/reactivate, or change the role of a staff account"),
    ("staff", "reset_password", "Reset a staff account's password"),
    ("rbac", "view", "View the permission catalog and roles"),
]

ADMIN_GRANTS = {(r, a) for r, a, _ in NEW_PERMISSIONS} | {("rbac", "manage")}

ENGINEER_GRANTS = {
    ("deployments", "view"), ("deployments", "create"), ("deployments", "renew"),
    ("deployments", "suspend"), ("deployments", "change_plan"), ("deployments", "extend_expiry"),
    ("deployments", "check_health"), ("deployments", "review_request"), ("deployments", "assign_staff"),
    ("tickets", "view"), ("tickets", "update_status"),
    ("maintenance", "view"), ("maintenance", "create"), ("maintenance", "update"), ("maintenance", "delete"),
    ("notifications", "view"), ("notifications", "resend"), ("notifications", "delete"), ("notifications", "test_send"),
}


def _grant(conn, role: str, resource: str, action: str) -> None:
    exists = conn.execute(
        sa.text("SELECT 1 FROM casbin_rule WHERE ptype = 'p' AND v0 = :role AND v1 = :domain AND v2 = :resource AND v3 = :action"),
        {"role": role, "domain": DOMAIN, "resource": resource, "action": action},
    ).scalar()
    if not exists:
        conn.execute(
            sa.text("INSERT INTO casbin_rule (ptype, v0, v1, v2, v3) VALUES ('p', :role, :domain, :resource, :action)"),
            {"role": role, "domain": DOMAIN, "resource": resource, "action": action},
        )


def _seed_permission(conn, resource: str, action: str, description: str) -> None:
    exists = conn.execute(
        sa.text("SELECT 1 FROM permissions WHERE resource = :resource AND action = :action"),
        {"resource": resource, "action": action},
    ).scalar()
    if not exists:
        conn.execute(
            sa.text(
                "INSERT INTO permissions (id, resource, action, description, created_at) "
                "VALUES (:id, :resource, :action, :description, now())"
            ),
            {"id": str(uuid.uuid4()), "resource": resource, "action": action, "description": description},
        )


def upgrade() -> None:
    conn = op.get_bind()

    for resource, action, description in NEW_PERMISSIONS:
        _seed_permission(conn, resource, action, description)

    for resource, action in RETIRED:
        conn.execute(sa.text(
            "DELETE FROM casbin_rule WHERE ptype = 'p' AND v1 = :domain AND v2 = :resource AND v3 = :action"
        ), {"domain": DOMAIN, "resource": resource, "action": action})
        conn.execute(sa.text(
            "DELETE FROM permissions WHERE resource = :resource AND action = :action"
        ), {"resource": resource, "action": action})

    for resource, action in ADMIN_GRANTS:
        _grant(conn, "admin", resource, action)
    for resource, action in ENGINEER_GRANTS:
        _grant(conn, "engineer", resource, action)


def downgrade() -> None:
    conn = op.get_bind()
    for resource, action, _ in NEW_PERMISSIONS:
        conn.execute(sa.text(
            "DELETE FROM casbin_rule WHERE ptype = 'p' AND v1 = :domain AND v2 = :resource AND v3 = :action"
        ), {"domain": DOMAIN, "resource": resource, "action": action})
        conn.execute(sa.text(
            "DELETE FROM permissions WHERE resource = :resource AND action = :action"
        ), {"resource": resource, "action": action})
    for resource, action in RETIRED:
        _seed_permission(conn, resource, action, f"{resource}.{action} (restored)")
        _grant(conn, "admin", resource, action)
        _grant(conn, "engineer", resource, action)
