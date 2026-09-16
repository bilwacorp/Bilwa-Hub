"""HUB-Expansion.md Phase 12/13 — DeploymentActionExecution/
DeploymentActionAttempt (app/models.py): tracks the deferred
deployment_client call for a gated renew/suspend/change_plan action
independently of the approval's own status (WorkflowInstance.status ==
completed no longer silently stands in for "and the deployment actually
received it"), with attempt history and a retry endpoint
(api/routers/deployments.py's retry_action_execution).

Seeds the new actions.retry permission: both 'admin' and 'engineer' get
it — same "keeps every action permission" pattern as prior migrations,
since retrying is an action a caller already had to be able to trigger
the original request for, not an admin-config permission.

Revision ID: 014_deployment_action_executions
Revises: 013_operational_events
Create Date: 2026-09-16
"""
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '014_deployment_action_executions'
down_revision: Union[str, None] = '013_operational_events'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DOMAIN = "default"

NEW_PERMISSIONS = [
    ("actions", "retry", "Retry a deployment action whose execution failed after approval"),
]
ADMIN_GRANTS = {(r, a) for r, a, _ in NEW_PERMISSIONS}
ENGINEER_GRANTS = {("actions", "retry")}


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
        "DO $$ BEGIN CREATE TYPE deploymentactionexecutionstatus AS ENUM "
        "('pending', 'executing', 'executed', 'failed'); "
        "EXCEPTION WHEN duplicate_object THEN NULL; END $$;"
    )
    op.execute(
        "DO $$ BEGIN CREATE TYPE deploymentactionattemptstatus AS ENUM ('success', 'failure'); "
        "EXCEPTION WHEN duplicate_object THEN NULL; END $$;"
    )
    execution_status = postgresql.ENUM(
        'pending', 'executing', 'executed', 'failed', name='deploymentactionexecutionstatus', create_type=False,
    )
    attempt_status = postgresql.ENUM('success', 'failure', name='deploymentactionattemptstatus', create_type=False)

    op.create_table(
        'deployment_action_executions',
        sa.Column('id', uuid_pk, primary_key=True),
        sa.Column('workflow_instance_id', uuid_pk, sa.ForeignKey('workflow_instances.id', ondelete='CASCADE'), nullable=False, unique=True),
        sa.Column('deployment_id', uuid_pk, sa.ForeignKey('deployments.id'), nullable=False),
        sa.Column('action_key', sa.String(50), nullable=False),
        sa.Column('idempotency_key', sa.String(80), nullable=False, unique=True),
        sa.Column('correlation_id', uuid_pk, nullable=True),
        sa.Column('status', execution_status, nullable=False, server_default='pending'),
        sa.Column('attempt_count', sa.Integer, nullable=False, server_default='0'),
        sa.Column('last_attempted_at', sa.DateTime, nullable=True),
        sa.Column('last_error', sa.Text, nullable=True),
        sa.Column('last_response', postgresql.JSON, nullable=True),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index('ix_deployment_action_executions_deployment_id', 'deployment_action_executions', ['deployment_id'])

    op.create_table(
        'deployment_action_attempts',
        sa.Column('id', uuid_pk, primary_key=True),
        sa.Column('execution_id', uuid_pk, sa.ForeignKey('deployment_action_executions.id', ondelete='CASCADE'), nullable=False),
        sa.Column('attempt_number', sa.Integer, nullable=False),
        sa.Column('status', attempt_status, nullable=False),
        sa.Column('error', sa.Text, nullable=True),
        sa.Column('response', postgresql.JSON, nullable=True),
        sa.Column('triggered_by', uuid_pk, sa.ForeignKey('users.id'), nullable=True),
        sa.Column('started_at', sa.DateTime, nullable=False),
        sa.Column('finished_at', sa.DateTime, nullable=False),
    )
    op.create_index('ix_deployment_action_attempts_execution_id', 'deployment_action_attempts', ['execution_id'])

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

    op.drop_index('ix_deployment_action_attempts_execution_id', table_name='deployment_action_attempts')
    op.drop_table('deployment_action_attempts')
    op.drop_index('ix_deployment_action_executions_deployment_id', table_name='deployment_action_executions')
    op.drop_table('deployment_action_executions')
    op.execute("DROP TYPE IF EXISTS deploymentactionattemptstatus")
    op.execute("DROP TYPE IF EXISTS deploymentactionexecutionstatus")
