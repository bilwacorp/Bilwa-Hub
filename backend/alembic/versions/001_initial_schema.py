"""Initial schema — users, casbin_rule (via the adapter's own metadata, not
managed here), deployments, deployment_snapshots, support_tickets,
maintenance_windows. See app/models.py for field-level rationale.

Revision ID: 001_initial_schema
Revises:
Create Date: 2026-09-10
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '001_initial_schema'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # casbin_async_sqlalchemy_adapter's Adapter never auto-creates its own
    # table (Adapter.create_table() is a separate opt-in call this app never
    # invokes) — so unlike relying on that, this migration creates it
    # explicitly, matching CasbinRule's exact column shape (adapter.py).
    op.create_table(
        'casbin_rule',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('ptype', sa.String(255), nullable=True),
        sa.Column('v0', sa.String(255), nullable=True),
        sa.Column('v1', sa.String(255), nullable=True),
        sa.Column('v2', sa.String(255), nullable=True),
        sa.Column('v3', sa.String(255), nullable=True),
        sa.Column('v4', sa.String(255), nullable=True),
        sa.Column('v5', sa.String(255), nullable=True),
    )

    op.create_table(
        'users',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('username', sa.String(50), nullable=False, unique=True),
        sa.Column('full_name', sa.String(100), nullable=True),
        sa.Column('email', sa.String(120), nullable=True, unique=True),
        sa.Column('hashed_password', sa.String(128), nullable=False),
        sa.Column('is_active', sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column('token_version', sa.Integer, nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
    )

    op.execute("DO $$ BEGIN CREATE TYPE deploymentstatus AS ENUM ('pending', 'active', 'suspended'); EXCEPTION WHEN duplicate_object THEN NULL; END $$;")
    op.create_table(
        'deployments',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('client_name', sa.String(200), nullable=False),
        sa.Column('slug', sa.String(100), nullable=False, unique=True),
        sa.Column('base_url', sa.String(500), nullable=True),
        sa.Column('status', postgresql.ENUM('pending', 'active', 'suspended', name='deploymentstatus', create_type=False), nullable=False, server_default='pending'),
        sa.Column('registration_token_hash', sa.String(64), nullable=False),
        sa.Column('registration_token_consumed_at', sa.DateTime, nullable=True),
        sa.Column('api_key_hash', sa.String(64), nullable=True),
        sa.Column('action_key_encrypted', sa.Text, nullable=True),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index('ix_deployments_registration_token_hash', 'deployments', ['registration_token_hash'])
    op.create_index('ix_deployments_api_key_hash', 'deployments', ['api_key_hash'])

    op.create_table(
        'deployment_snapshots',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('deployment_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('deployments.id'), nullable=False),
        sa.Column('app_version', sa.String(50), nullable=True),
        sa.Column('plan_name', sa.String(100), nullable=True),
        sa.Column('subscription_status', sa.String(50), nullable=True),
        sa.Column('expiry_date', sa.Date, nullable=True),
        sa.Column('trial_ends_at', sa.Date, nullable=True),
        sa.Column('auto_renew', sa.Boolean, nullable=True),
        sa.Column('usage', postgresql.JSON, nullable=True),
        sa.Column('pending_requests', postgresql.JSON, nullable=True),
        sa.Column('received_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index('ix_deployment_snapshots_deployment_id', 'deployment_snapshots', ['deployment_id'])
    op.create_index('ix_deployment_snapshots_received_at', 'deployment_snapshots', ['received_at'])

    op.execute("DO $$ BEGIN CREATE TYPE supportticketstatus AS ENUM ('open', 'in_progress', 'resolved', 'closed'); EXCEPTION WHEN duplicate_object THEN NULL; END $$;")
    op.create_table(
        'support_tickets',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('deployment_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('deployments.id'), nullable=False),
        sa.Column('subject', sa.String(300), nullable=False),
        sa.Column('description', sa.Text, nullable=False),
        sa.Column('priority', sa.String(20), nullable=False, server_default='normal'),
        sa.Column('status', postgresql.ENUM('open', 'in_progress', 'resolved', 'closed', name='supportticketstatus', create_type=False), nullable=False, server_default='open'),
        sa.Column('submitted_by_name', sa.String(200), nullable=True),
        sa.Column('submitted_by_email', sa.String(200), nullable=True),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column('resolved_at', sa.DateTime, nullable=True),
    )
    op.create_index('ix_support_tickets_deployment_id', 'support_tickets', ['deployment_id'])

    op.execute("DO $$ BEGIN CREATE TYPE maintenancewindowstatus AS ENUM ('planned', 'in_progress', 'completed', 'cancelled'); EXCEPTION WHEN duplicate_object THEN NULL; END $$;")
    op.create_table(
        'maintenance_windows',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('deployment_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('deployments.id'), nullable=True),
        sa.Column('scheduled_start', sa.DateTime, nullable=False),
        sa.Column('scheduled_end', sa.DateTime, nullable=False),
        sa.Column('description', sa.Text, nullable=False),
        sa.Column('status', postgresql.ENUM('planned', 'in_progress', 'completed', 'cancelled', name='maintenancewindowstatus', create_type=False), nullable=False, server_default='planned'),
        sa.Column('created_by', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table('casbin_rule')
    op.drop_table('maintenance_windows')
    op.drop_table('support_tickets')
    op.drop_table('deployment_snapshots')
    op.drop_table('deployments')
    op.drop_table('users')
    op.execute("DROP TYPE IF EXISTS maintenancewindowstatus")
    op.execute("DROP TYPE IF EXISTS supportticketstatus")
    op.execute("DROP TYPE IF EXISTS deploymentstatus")
