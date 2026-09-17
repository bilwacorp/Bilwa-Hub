"""HUB-Expansion.md Phase 10 — Operations Dashboard. No new tables (pure
aggregation over existing data — see app/services/dashboard.py); this
migration only seeds the dashboard.view permission. Both 'admin' and
'engineer' get it — same "keeps every view-ish permission it already
effectively had" pattern as github.view (migration 015): the dashboard's
own row-level scoping (see services/dashboard.py) already narrows an
engineer's numbers to their assigned deployments, so granting the
permission itself doesn't leak anything a plain deployments.view
wouldn't already.

Revision ID: 019_dashboard_permission
Revises: 018_maintenance_lifecycle
Create Date: 2026-09-17
"""
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '019_dashboard_permission'
down_revision: Union[str, None] = '018_maintenance_lifecycle'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DOMAIN = "default"
NEW_PERMISSIONS = [("dashboard", "view", "View the operations dashboard")]


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
    for role in ("admin", "engineer"):
        for resource, action, _ in NEW_PERMISSIONS:
            _grant(conn, role, resource, action)


def downgrade() -> None:
    conn = op.get_bind()
    for resource, action, _ in NEW_PERMISSIONS:
        conn.execute(sa.text(
            "DELETE FROM casbin_rule WHERE ptype = 'p' AND v1 = :domain AND v2 = :resource AND v3 = :action"
        ), {"domain": DOMAIN, "resource": resource, "action": action})
        conn.execute(sa.text(
            "DELETE FROM permissions WHERE resource = :resource AND action = :action"
        ), {"resource": resource, "action": action})
