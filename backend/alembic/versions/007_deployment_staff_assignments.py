"""deployment_staff_assignments — many-to-many staff <-> deployment
assignment, used to narrow notification fan-out (see
app/services/notifications/recipients.py's recipients_for_deployment).

Revision ID: 007_deployment_staff
Revises: 006_notifications
Create Date: 2026-09-16
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '007_deployment_staff'
down_revision: Union[str, None] = '006_notifications'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'deployment_staff_assignments',
        sa.Column('deployment_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('deployments.id'), primary_key=True),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id'), primary_key=True),
        sa.Column('assigned_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index('ix_deployment_staff_assignments_user_id', 'deployment_staff_assignments', ['user_id'])


def downgrade() -> None:
    op.drop_table('deployment_staff_assignments')
