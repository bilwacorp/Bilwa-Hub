"""maintenance_windows: read_only + push_state, for the scheduler/push
feature (see app/core/maintenance_scheduler.py). No new statuses — the
existing enum is untouched.

Revision ID: 003_maintenance_scheduling
Revises: 002_seed_admin
Create Date: 2026-09-10
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '003_maintenance_scheduling'
down_revision: Union[str, None] = '002_seed_admin'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'maintenance_windows',
        sa.Column('read_only', sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        'maintenance_windows',
        sa.Column('push_state', postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.create_index('ix_maintenance_windows_status', 'maintenance_windows', ['status'])


def downgrade() -> None:
    op.drop_index('ix_maintenance_windows_status', table_name='maintenance_windows')
    op.drop_column('maintenance_windows', 'push_state')
    op.drop_column('maintenance_windows', 'read_only')
