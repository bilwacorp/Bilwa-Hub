"""Seed a single BilwaCorp staff admin user + the one Phase 1 permission
grant (STAFF_MANAGE — see core/permissions.py). Mirrors PoultryOS-CBP's
046_seed_admin_user.py idempotent pattern.

Username: admin   Password: ChangeMe@2026
Change this immediately after first login via a future PATCH /auth/me (not
built in Phase 1 — for now, update the users table directly or re-run this
migration's INSERT by hand with a new bcrypt hash).

Revision ID: 002_seed_admin
Revises: 001_initial_schema
Create Date: 2026-09-10
"""
from typing import Sequence, Union
import uuid

import bcrypt
from alembic import op
import sqlalchemy as sa

revision: str = '002_seed_admin'
down_revision: Union[str, None] = '001_initial_schema'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DOMAIN = "default"
USERNAME = "admin"
PASSWORD = "ChangeMe@2026"


def upgrade() -> None:
    conn = op.get_bind()

    existing_id = conn.execute(sa.text("SELECT id FROM users WHERE username = :u"), {"u": USERNAME}).scalar()
    if existing_id is None:
        user_id = uuid.uuid4()
        hashed = bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt()).decode()
        conn.execute(
            sa.text(
                "INSERT INTO users (id, username, full_name, hashed_password, is_active, token_version, created_at) "
                "VALUES (:id, :username, :full_name, :hashed, true, 0, now())"
            ),
            {"id": user_id, "username": USERNAME, "full_name": "BilwaCorp Admin", "hashed": hashed},
        )
    else:
        user_id = existing_id

    already_granted = conn.execute(
        sa.text("SELECT 1 FROM casbin_rule WHERE ptype = 'g' AND v0 = :v0 AND v1 = 'admin' AND v2 = :domain"),
        {"v0": str(user_id), "domain": DOMAIN},
    ).scalar()
    if not already_granted:
        conn.execute(
            sa.text("INSERT INTO casbin_rule (ptype, v0, v1, v2) VALUES ('g', :v0, 'admin', :domain)"),
            {"v0": str(user_id), "domain": DOMAIN},
        )

    already_has_perm = conn.execute(
        sa.text("SELECT 1 FROM casbin_rule WHERE ptype = 'p' AND v0 = 'admin' AND v1 = :domain AND v2 = 'staff' AND v3 = 'manage'"),
        {"domain": DOMAIN},
    ).scalar()
    if not already_has_perm:
        conn.execute(
            sa.text("INSERT INTO casbin_rule (ptype, v0, v1, v2, v3) VALUES ('p', 'admin', :domain, 'staff', 'manage')"),
            {"domain": DOMAIN},
        )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("DELETE FROM casbin_rule WHERE ptype = 'p' AND v0 = 'admin' AND v2 = 'staff' AND v3 = 'manage'"))
    row_id = conn.execute(sa.text("SELECT id FROM users WHERE username = :u"), {"u": USERNAME}).scalar()
    if row_id is not None:
        conn.execute(sa.text("DELETE FROM casbin_rule WHERE ptype = 'g' AND v0 = :v0"), {"v0": str(row_id)})
        conn.execute(sa.text("DELETE FROM users WHERE id = :id"), {"id": row_id})
