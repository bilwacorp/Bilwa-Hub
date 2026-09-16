"""HUB-Expansion.md Phase 1 — the cross-domain OperationalEvent log
(app/models.py's OperationalEvent) plus workflow_instances.correlation_id
(so an approval's request/approve/execute events can be tied together —
see app/approvals/integration.py's start_approval).

Seeds the new events.view/events.view_all permissions: 'admin' gets both,
'engineer' gets events.view only — same "keeps every action permission,
not the fleet-wide-visibility ones" pattern as migrations 011/012.

Revision ID: 013_operational_events
Revises: 012_workflow_engine
Create Date: 2026-09-16
"""
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '013_operational_events'
down_revision: Union[str, None] = '012_workflow_engine'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DOMAIN = "default"

NEW_PERMISSIONS = [
    ("events", "view", "View the operational event log for deployments assigned to you (non-deployment events are always visible)"),
    ("events", "view_all", "View the operational event log for every deployment"),
]
ADMIN_GRANTS = {(r, a) for r, a, _ in NEW_PERMISSIONS}
ENGINEER_GRANTS = {("events", "view")}


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
    uuid_pk = postgresql.UUID(as_uuid=True)

    op.execute(
        "DO $$ BEGIN CREATE TYPE operationaleventstatus AS ENUM ('info', 'pending', 'success', 'failure'); "
        "EXCEPTION WHEN duplicate_object THEN NULL; END $$;"
    )
    operational_event_status = postgresql.ENUM('info', 'pending', 'success', 'failure', name='operationaleventstatus', create_type=False)

    op.create_table(
        'operational_events',
        sa.Column('id', uuid_pk, primary_key=True),
        sa.Column('event_type', sa.String(100), nullable=False),
        sa.Column('source', sa.String(50), nullable=False),
        sa.Column('actor_type', sa.String(30), nullable=False),
        sa.Column('actor_id', uuid_pk, nullable=True),
        sa.Column('entity_type', sa.String(50), nullable=True),
        sa.Column('entity_id', uuid_pk, nullable=True),
        sa.Column('deployment_id', uuid_pk, sa.ForeignKey('deployments.id'), nullable=True),
        sa.Column('customer_id', uuid_pk, nullable=True),
        sa.Column('correlation_id', uuid_pk, nullable=False),
        sa.Column('causation_id', uuid_pk, nullable=True),
        sa.Column('status', operational_event_status, nullable=False, server_default='info'),
        sa.Column('event_metadata', postgresql.JSON, nullable=True),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index('ix_operational_events_event_type', 'operational_events', ['event_type'])
    op.create_index('ix_operational_events_deployment_id', 'operational_events', ['deployment_id'])
    op.create_index('ix_operational_events_correlation_id', 'operational_events', ['correlation_id'])
    op.create_index('ix_operational_events_created_at', 'operational_events', ['created_at'])

    op.add_column('workflow_instances', sa.Column('correlation_id', uuid_pk, nullable=True))

    for resource, action, description in NEW_PERMISSIONS:
        _seed_permission(conn, resource, action, description)
    for resource, action in ADMIN_GRANTS:
        _grant(conn, "admin", resource, action)
    for resource, action in ENGINEER_GRANTS:
        _grant(conn, "engineer", resource, action)


def downgrade() -> None:
    conn = op.get_bind()
    for resource, action, _ in NEW_PERMISSIONS:
        conn.execute(sa.text(
            "DELETE FROM casbin_rule WHERE ptype = 'p' AND v1 = :domain AND v2 = :resource AND v3 = :action"
        ), {"domain": DOMAIN, "resource": resource, "action": action})
        conn.execute(sa.text(
            "DELETE FROM permissions WHERE resource = :resource AND action = :action"
        ), {"resource": resource, "action": action})

    op.drop_column('workflow_instances', 'correlation_id')

    op.drop_index('ix_operational_events_created_at', table_name='operational_events')
    op.drop_index('ix_operational_events_correlation_id', table_name='operational_events')
    op.drop_index('ix_operational_events_deployment_id', table_name='operational_events')
    op.drop_index('ix_operational_events_event_type', table_name='operational_events')
    op.drop_table('operational_events')
    op.execute("DROP TYPE IF EXISTS operationaleventstatus")
