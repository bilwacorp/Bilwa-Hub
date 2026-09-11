"""maintenance_windows: replace the read_only bool with a `mode` string
(banner | read_only | lockout) so a window can also block new sign-ins,
not just freeze writes. Existing read_only=true rows become mode='read_only'.

Revision ID: 004_maintenance_mode
Revises: 003_maintenance_scheduling
Create Date: 2026-09-10
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '004_maintenance_mode'
down_revision: Union[str, None] = '003_maintenance_scheduling'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'maintenance_windows',
        sa.Column('mode', sa.String(length=16), nullable=False, server_default='banner'),
    )
    op.execute("UPDATE maintenance_windows SET mode = 'read_only' WHERE read_only IS TRUE")
    op.drop_column('maintenance_windows', 'read_only')


def downgrade() -> None:
    op.add_column(
        'maintenance_windows',
        sa.Column('read_only', sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.execute("UPDATE maintenance_windows SET read_only = TRUE WHERE mode IN ('read_only', 'lockout')")
    op.drop_column('maintenance_windows', 'mode')
