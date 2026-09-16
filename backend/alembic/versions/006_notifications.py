"""Notifications: users.phone (WhatsApp recipient) + notification_logs —
see app/services/notifications/. No new Casbin permission; the admin API is
gated by the existing FLEET_MANAGE (core/permissions.py).

Revision ID: 006_notifications
Revises: 005_staff_roles
Create Date: 2026-09-16
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '006_notifications'
down_revision: Union[str, None] = '005_staff_roles'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('phone', sa.String(length=20), nullable=True))

    op.execute(
        "DO $$ BEGIN CREATE TYPE notificationchannel AS ENUM ('email', 'whatsapp'); "
        "EXCEPTION WHEN duplicate_object THEN NULL; END $$;"
    )
    op.execute(
        "DO $$ BEGIN CREATE TYPE notificationstatus AS ENUM "
        "('pending', 'sending', 'sent', 'failed', 'cancelled'); "
        "EXCEPTION WHEN duplicate_object THEN NULL; END $$;"
    )
    op.create_table(
        'notification_logs',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('channel', postgresql.ENUM('email', 'whatsapp', name='notificationchannel', create_type=False), nullable=False),
        sa.Column('provider', sa.String(30), nullable=False),
        sa.Column('recipient', sa.String(200), nullable=False),
        sa.Column('subject', sa.String(255), nullable=True),
        sa.Column('template', sa.String(100), nullable=True),
        sa.Column('payload', sa.Text, nullable=True),
        sa.Column(
            'status',
            postgresql.ENUM('pending', 'sending', 'sent', 'failed', 'cancelled', name='notificationstatus', create_type=False),
            nullable=False, server_default='pending',
        ),
        sa.Column('error_message', sa.Text, nullable=True),
        sa.Column('retry_count', sa.Integer, nullable=False, server_default='0'),
        sa.Column('sent_at', sa.DateTime, nullable=True),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index('ix_notification_logs_created_at', 'notification_logs', ['created_at'])
    op.create_index('ix_notification_logs_status', 'notification_logs', ['status'])
    op.create_index('ix_notification_logs_recipient', 'notification_logs', ['recipient'])


def downgrade() -> None:
    op.drop_table('notification_logs')
    op.execute("DROP TYPE IF EXISTS notificationstatus")
    op.execute("DROP TYPE IF EXISTS notificationchannel")
    op.drop_column('users', 'phone')
