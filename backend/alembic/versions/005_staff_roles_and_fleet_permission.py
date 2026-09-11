"""Split STAFF_MANAGE into STAFF_MANAGE (staff/user management) + a new
FLEET_MANAGE permission (deployments/tickets/maintenance) so a second
'engineer' role can be granted fleet access without also getting staff
management (see core/permissions.py, services/rbac.py). The existing
'admin' role only had staff:manage before this — grant it fleet:manage
too so the seeded admin (002_seed_admin.py) doesn't lose fleet access.
No user rows change; engineers are created afterward via POST /users.

Revision ID: 005_staff_roles
Revises: 004_maintenance_mode
Create Date: 2026-09-11
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '005_staff_roles'
down_revision: Union[str, None] = '004_maintenance_mode'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DOMAIN = "default"


def _grant(conn, role: str) -> None:
    exists = conn.execute(
        sa.text("SELECT 1 FROM casbin_rule WHERE ptype = 'p' AND v0 = :role AND v1 = :domain AND v2 = 'fleet' AND v3 = 'manage'"),
        {"role": role, "domain": DOMAIN},
    ).scalar()
    if not exists:
        conn.execute(
            sa.text("INSERT INTO casbin_rule (ptype, v0, v1, v2, v3) VALUES ('p', :role, :domain, 'fleet', 'manage')"),
            {"role": role, "domain": DOMAIN},
        )


def upgrade() -> None:
    conn = op.get_bind()
    _grant(conn, "admin")
    _grant(conn, "engineer")


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text(
        "DELETE FROM casbin_rule WHERE ptype = 'p' AND v1 = :domain AND v2 = 'fleet' AND v3 = 'manage' AND v0 IN ('admin', 'engineer')"
    ), {"domain": DOMAIN})
