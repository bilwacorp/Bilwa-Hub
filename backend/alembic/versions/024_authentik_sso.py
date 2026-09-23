"""Staff login moves to Authentik OIDC SSO, replacing username/password
entirely — see docs/adr/ADR-012-authentik-sso.md. No auto-provisioning:
an admin still pre-creates the User row (by email) before someone can
sign in.

users.hashed_password becomes nullable rather than dropped — a clean
column removal was considered and rejected in favor of this lower-risk
option, since a destructive drop is much harder to walk back on a live
deployment than a nullability change. password_reset_token_hash /
password_reset_expires_at were already nullable and are left as-is
(unused dead columns, not touched) for the same reason.

The staff.reset_password permission is retired (mirrors migration 011's
RETIRED pattern) since api/routers/users.py's POST /{user_id}/reset-
password endpoint no longer exists.

Revision ID: 024_authentik_sso
Revises: 023_github_app_config
Create Date: 2026-09-20
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '024_authentik_sso'
down_revision: Union[str, None] = '023_github_app_config'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DOMAIN = "default"


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
                "VALUES (gen_random_uuid(), :resource, :action, :description, now())"
            ),
            {"resource": resource, "action": action, "description": description},
        )


def upgrade() -> None:
    op.alter_column('users', 'hashed_password', existing_type=sa.String(128), nullable=True)

    conn = op.get_bind()
    conn.execute(sa.text(
        "DELETE FROM casbin_rule WHERE ptype = 'p' AND v1 = :domain AND v2 = 'staff' AND v3 = 'reset_password'"
    ), {"domain": DOMAIN})
    conn.execute(sa.text("DELETE FROM permissions WHERE resource = 'staff' AND action = 'reset_password'"))


def downgrade() -> None:
    # Best-effort only: any User row created after this migration ran has
    # hashed_password = NULL, so this ALTER will fail if such a row exists
    # — a real rollback needs those rows backfilled or removed first.
    op.alter_column('users', 'hashed_password', existing_type=sa.String(128), nullable=False)

    conn = op.get_bind()
    _seed_permission(conn, "staff", "reset_password", "Reset a staff account's password")
    _grant(conn, "admin", "staff", "reset_password")
