"""deployments.expiry_reminder_sent_for — idempotency tracking for the new
subscription-expiring-soon email/WhatsApp alert (see
app/core/expiry_reminder_scheduler.py).

Revision ID: 008_expiry_reminder
Revises: 007_deployment_staff
Create Date: 2026-09-17
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '008_expiry_reminder'
down_revision: Union[str, None] = '007_deployment_staff'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('deployments', sa.Column('expiry_reminder_sent_for', sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column('deployments', 'expiry_reminder_sent_for')
