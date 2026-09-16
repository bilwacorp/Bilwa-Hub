"""RBAC catalog + custom roles — adds `roles` (dynamic, admin-creatable
metadata; casbin_rule stays the enforcement source of truth) and
`permissions` (the fixed, migration-seeded catalog the admin UI's checkbox
grid renders against — see core/permissions.py's ALL_PERMISSIONS).

Behavior-preserving cutover, not a permissions redesign: the old coarse
FLEET_MANAGE ('fleet', 'manage') is split into one permission per resource
(deployments/tickets/maintenance/notifications), and 'admin'/'engineer' are
re-granted the exact same *effective* access they already had — 'admin'
gets all six new permissions (staff, the four fleet ones, and the new
rbac.manage for this feature itself), 'engineer' gets the four fleet ones
only, matching what FLEET_MANAGE already covered for it. No user's actual
access changes as a result of this migration.

Revision ID: 010_rbac_catalog
Revises: 009_password_reset
Create Date: 2026-09-18
"""
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '010_rbac_catalog'
down_revision: Union[str, None] = '009_password_reset'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DOMAIN = "default"

# (resource, action, description) — kept in sync with
# core/permissions.py's ALL_PERMISSIONS by hand (this migration is the
# seed; that module is what the running app actually imports).
PERMISSIONS = [
    ("staff", "manage", "Create, deactivate, and reset the password of staff accounts; assign roles"),
    ("deployments", "manage", "Register client deployments, run renew/suspend/change-plan/extend-expiry actions, review subscription requests, assign staff to a deployment"),
    ("tickets", "manage", "View and update the status of support tickets relayed from client deployments"),
    ("maintenance", "manage", "Create and edit fleet maintenance windows"),
    ("notifications", "manage", "View notification history, resend/delete entries, send test emails/WhatsApp messages"),
    ("rbac", "manage", "Create/delete custom roles and change which permissions any role holds"),
]

ROLES = [
    ("admin", "Full access, including staff management and role/permission editing.", True),
    ("engineer", "Fleet access (deployments, tickets, maintenance, notifications) — cannot manage staff or roles.", True),
]

# role -> set of (resource, action) it's granted at cutover
ROLE_GRANTS = {
    "admin": {(r, a) for r, a, _ in PERMISSIONS},
    "engineer": {("deployments", "manage"), ("tickets", "manage"), ("maintenance", "manage"), ("notifications", "manage")},
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


def upgrade() -> None:
    conn = op.get_bind()

    op.create_table(
        'roles',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('name', sa.String(50), nullable=False, unique=True),
        sa.Column('description', sa.Text, nullable=True),
        sa.Column('is_system', sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        'permissions',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('resource', sa.String(100), nullable=False),
        sa.Column('action', sa.String(100), nullable=False),
        sa.Column('description', sa.Text, nullable=True),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint('resource', 'action', name='uq_permissions_resource_action'),
    )

    # Seed the permission catalog.
    for resource, action, description in PERMISSIONS:
        conn.execute(
            sa.text(
                "INSERT INTO permissions (id, resource, action, description, created_at) "
                "VALUES (:id, :resource, :action, :description, now())"
            ),
            {"id": str(uuid.uuid4()), "resource": resource, "action": action, "description": description},
        )

    # Seed role metadata (id -> name lookup only needed within this
    # migration; casbin_rule references roles by name, not id).
    for name, description, is_system in ROLES:
        conn.execute(
            sa.text(
                "INSERT INTO roles (id, name, description, is_system, created_at, updated_at) "
                "VALUES (:id, :name, :description, :is_system, now(), now())"
            ),
            {"id": str(uuid.uuid4()), "name": name, "description": description, "is_system": is_system},
        )

    # Cutover: drop the old coarse fleet:manage grant, re-grant the split
    # permissions so 'admin'/'engineer' end up with identical effective
    # access to what they had before this migration.
    conn.execute(sa.text(
        "DELETE FROM casbin_rule WHERE ptype = 'p' AND v1 = :domain AND v2 = 'fleet' AND v3 = 'manage'"
    ), {"domain": DOMAIN})
    for role, grants in ROLE_GRANTS.items():
        for resource, action in grants:
            _grant(conn, role, resource, action)


def downgrade() -> None:
    conn = op.get_bind()
    # Restore the coarse fleet:manage grant for whichever roles held any of
    # the split permissions, then drop the split grants + catalog tables.
    for role in ROLE_GRANTS:
        _grant(conn, role, "fleet", "manage")
    for resource, action, _ in PERMISSIONS:
        if (resource, action) == ("staff", "manage"):
            continue  # pre-existed this migration, leave it alone
        conn.execute(sa.text(
            "DELETE FROM casbin_rule WHERE ptype = 'p' AND v1 = :domain AND v2 = :resource AND v3 = :action"
        ), {"domain": DOMAIN, "resource": resource, "action": action})
    op.drop_table('permissions')
    op.drop_table('roles')
