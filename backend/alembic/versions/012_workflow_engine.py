"""Embedded BPMN workflow + approval engine (ported from PoultryPro-CBF —
SpiffWorkflow + a sandboxed rule-engine for approval routing), gating
deployment renew/suspend/change-plan. See core/permissions.py's new
WORKFLOWS_*/WORKFLOW_RULES_*/WORKFLOW_INSTANCES_*/APPROVALS_* permissions
and app/approvals/deployment_hooks.py for how the gate is wired in.

Creates the nine workflow/rules tables (no branch_id anywhere — this hub
is single-org), seeds the new permission catalog rows + grants ('admin'
gets everything; 'engineer' gets approvals.view/act + workflow_instances
.view only — same "keeps every action permission, not the admin-config
ones" pattern as migration 011), and seeds three built-in
(is_system=True), published, single-step workflow definitions —
`deployment_renew` / `deployment_suspend` / `deployment_change_plan` —
each with one approval_rule routing to the 'admin' Casbin role. The gate
is "soft" from day one: any admin can immediately flip the definition's
is_active off (or unpublish/replace its version) from the Workflows page
to fall back to today's un-gated behavior, without another migration.

Revision ID: 012_workflow_engine
Revises: 011_granular_permissions
Create Date: 2026-09-20
"""
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '012_workflow_engine'
down_revision: Union[str, None] = '011_granular_permissions'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DOMAIN = "default"

NEW_PERMISSIONS = [
    ("workflows", "view", "View workflow definitions and their BPMN diagrams"),
    ("workflows", "create", "Create a new workflow definition"),
    ("workflows", "update", "Edit a workflow definition or its draft versions"),
    ("workflows", "publish", "Publish a workflow version"),
    ("workflows", "archive", "Archive a workflow version"),
    ("workflows", "delete", "Delete a workflow definition"),
    ("workflow_rules", "view", "View approval routing rules"),
    ("workflow_rules", "create", "Create an approval routing rule"),
    ("workflow_rules", "update", "Edit an approval routing rule"),
    ("workflow_rules", "delete", "Delete an approval routing rule"),
    ("workflow_rules", "test", "Test an approval routing rule against sample input"),
    ("workflow_instances", "view", "View running/finished workflow instances and their history"),
    ("workflow_instances", "cancel", "Cancel a running workflow instance"),
    ("approvals", "view", "View the approvals inbox"),
    ("approvals", "act", "Approve or reject a task assigned/candidate to you"),
    ("approvals", "reassign", "Reassign a pending approval task to another user"),
    ("approvals", "act_any", "Approve or reject any pending task, not just ones you're a candidate for"),
]

ADMIN_GRANTS = {(r, a) for r, a, _ in NEW_PERMISSIONS}
ENGINEER_GRANTS = {("workflow_instances", "view"), ("approvals", "view"), ("approvals", "act")}

# One BPMN process per gated action: a start event -> a single user task
# (stepKey = "<key>_approval") -> end event. Kept intentionally minimal —
# an admin can replace this with a multi-step diagram later via the
# designer; this is the smallest thing that actually gates the action.
_BPMN_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"
                  xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI"
                  xmlns:omgdc="http://www.omg.org/spec/DD/20100524/DC"
                  xmlns:spiffworkflow="http://spiffworkflow.org/bpmn/schema/1.0/core"
                  id="Definitions_{key}" targetNamespace="http://bpmn.io/schema/bpmn">
  <bpmn:process id="{key}" isExecutable="true">
    <bpmn:startEvent id="StartEvent_1" name="{start_name}">
      <bpmn:outgoing>Flow_1</bpmn:outgoing>
    </bpmn:startEvent>
    <bpmn:userTask id="Approve_1" name="{task_name}">
      <bpmn:extensionElements>
        <spiffworkflow:properties>
          <spiffworkflow:property name="stepKey" value="{key}_approval" />
        </spiffworkflow:properties>
      </bpmn:extensionElements>
      <bpmn:incoming>Flow_1</bpmn:incoming>
      <bpmn:outgoing>Flow_2</bpmn:outgoing>
    </bpmn:userTask>
    <bpmn:endEvent id="EndEvent_1" name="Completed">
      <bpmn:incoming>Flow_2</bpmn:incoming>
    </bpmn:endEvent>
    <bpmn:sequenceFlow id="Flow_1" sourceRef="StartEvent_1" targetRef="Approve_1" />
    <bpmn:sequenceFlow id="Flow_2" sourceRef="Approve_1" targetRef="EndEvent_1" />
  </bpmn:process>
  <bpmndi:BPMNDiagram id="BPMNDiagram_1">
    <bpmndi:BPMNPlane id="BPMNPlane_1" bpmnElement="{key}">
      <bpmndi:BPMNShape id="StartEvent_1_di" bpmnElement="StartEvent_1">
        <omgdc:Bounds x="152" y="102" width="36" height="36" />
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="Approve_1_di" bpmnElement="Approve_1">
        <omgdc:Bounds x="260" y="80" width="100" height="80" />
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="EndEvent_1_di" bpmnElement="EndEvent_1">
        <omgdc:Bounds x="432" y="102" width="36" height="36" />
      </bpmndi:BPMNShape>
      <bpmndi:BPMNEdge id="Flow_1_di" bpmnElement="Flow_1">
        <omgdi:waypoint xmlns:omgdi="http://www.omg.org/spec/DD/20100524/DI" x="188" y="120" />
        <omgdi:waypoint xmlns:omgdi="http://www.omg.org/spec/DD/20100524/DI" x="260" y="120" />
      </bpmndi:BPMNEdge>
      <bpmndi:BPMNEdge id="Flow_2_di" bpmnElement="Flow_2">
        <omgdi:waypoint xmlns:omgdi="http://www.omg.org/spec/DD/20100524/DI" x="360" y="120" />
        <omgdi:waypoint xmlns:omgdi="http://www.omg.org/spec/DD/20100524/DI" x="432" y="120" />
      </bpmndi:BPMNEdge>
    </bpmndi:BPMNPlane>
  </bpmndi:BPMNDiagram>
</bpmn:definitions>
"""

# key -> (definition name, description, start event label, task label)
SEEDED_WORKFLOWS = {
    "deployment_renew": (
        "Deployment Renewal Approval", "Requires approval before renewing a deployment's subscription.",
        "Renewal Requested", "Approve Renewal",
    ),
    "deployment_suspend": (
        "Deployment Suspension Approval", "Requires approval before suspending a deployment's subscription.",
        "Suspension Requested", "Approve Suspension",
    ),
    "deployment_change_plan": (
        "Deployment Plan Change Approval", "Requires approval before changing a deployment's plan.",
        "Plan Change Requested", "Approve Plan Change",
    ),
}


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

    # Native Postgres enum types, matching the naming SQLAlchemy's generic
    # Enum(SomePythonEnum) derives by default (lowercased class name) —
    # same DO $$ ... EXCEPTION pattern as migration 001, so a matching
    # model-side `mapped_column(Enum(...))` binds/reads correctly instead
    # of expecting a native type that was never created.
    op.execute("DO $$ BEGIN CREATE TYPE workflowversionstatus AS ENUM ('draft', 'published', 'archived'); EXCEPTION WHEN duplicate_object THEN NULL; END $$;")
    op.execute("DO $$ BEGIN CREATE TYPE instancestatus AS ENUM ('running', 'completed', 'rejected', 'cancelled', 'error'); EXCEPTION WHEN duplicate_object THEN NULL; END $$;")
    op.execute("DO $$ BEGIN CREATE TYPE workflowtaskstatus AS ENUM ('pending', 'approved', 'rejected', 'cancelled', 'skipped'); EXCEPTION WHEN duplicate_object THEN NULL; END $$;")
    op.execute(
        "DO $$ BEGIN CREATE TYPE historyeventtype AS ENUM ("
        "'instance_started', 'task_created', 'task_approved', 'task_rejected', 'task_auto_approved', "
        "'task_skipped', 'task_reassigned', 'task_cancelled', 'instance_completed', 'instance_rejected', "
        "'instance_cancelled', 'instance_error', 'rule_error', 'variable_updated'"
        "); EXCEPTION WHEN duplicate_object THEN NULL; END $$;"
    )
    op.execute("DO $$ BEGIN CREATE TYPE ruleactiontype AS ENUM ('assign_approver', 'auto_approve', 'skip_step', 'set_variable'); EXCEPTION WHEN duplicate_object THEN NULL; END $$;")
    op.execute("DO $$ BEGIN CREATE TYPE approverstrategy AS ENUM ('casbin_role', 'explicit_users'); EXCEPTION WHEN duplicate_object THEN NULL; END $$;")

    workflow_version_status = postgresql.ENUM('draft', 'published', 'archived', name='workflowversionstatus', create_type=False)
    instance_status = postgresql.ENUM('running', 'completed', 'rejected', 'cancelled', 'error', name='instancestatus', create_type=False)
    workflow_task_status = postgresql.ENUM('pending', 'approved', 'rejected', 'cancelled', 'skipped', name='workflowtaskstatus', create_type=False)
    history_event_type = postgresql.ENUM(
        'instance_started', 'task_created', 'task_approved', 'task_rejected', 'task_auto_approved',
        'task_skipped', 'task_reassigned', 'task_cancelled', 'instance_completed', 'instance_rejected',
        'instance_cancelled', 'instance_error', 'rule_error', 'variable_updated',
        name='historyeventtype', create_type=False,
    )
    rule_action_type = postgresql.ENUM('assign_approver', 'auto_approve', 'skip_step', 'set_variable', name='ruleactiontype', create_type=False)
    approver_strategy = postgresql.ENUM('casbin_role', 'explicit_users', name='approverstrategy', create_type=False)

    op.create_table(
        'workflow_definitions',
        sa.Column('id', uuid_pk, primary_key=True),
        sa.Column('key', sa.String(100), nullable=False, unique=True),
        sa.Column('name', sa.String(200), nullable=False),
        sa.Column('description', sa.Text, nullable=True),
        sa.Column('is_active', sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column('is_system', sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column('created_by', uuid_pk, sa.ForeignKey('users.id'), nullable=True),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        'workflow_versions',
        sa.Column('id', uuid_pk, primary_key=True),
        sa.Column('definition_id', uuid_pk, sa.ForeignKey('workflow_definitions.id', ondelete='CASCADE'), nullable=False),
        sa.Column('version', sa.Integer, nullable=False),
        sa.Column('bpmn_xml', sa.Text, nullable=False),
        sa.Column('process_id', sa.String(200), nullable=False),
        sa.Column('status', workflow_version_status, nullable=False, server_default='draft'),
        sa.Column('notes', sa.Text, nullable=True),
        sa.Column('created_by', uuid_pk, sa.ForeignKey('users.id'), nullable=True),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column('published_at', sa.DateTime, nullable=True),
        sa.Column('published_by', uuid_pk, sa.ForeignKey('users.id'), nullable=True),
        sa.UniqueConstraint('definition_id', 'version', name='uq_workflow_version_number'),
    )
    # Only one published version per definition at a time — enforced by a
    # partial unique index (Postgres treats each NULL as distinct, so a
    # plain UNIQUE(definition_id, status) would not work here).
    op.create_index(
        'uq_workflow_definition_published', 'workflow_versions', ['definition_id'],
        unique=True, postgresql_where=sa.text("status = 'published'"),
    )

    op.create_table(
        'workflow_instances',
        sa.Column('id', uuid_pk, primary_key=True),
        sa.Column('instance_code', sa.String(30), nullable=False, unique=True),
        sa.Column('definition_id', uuid_pk, sa.ForeignKey('workflow_definitions.id'), nullable=False),
        sa.Column('version_id', uuid_pk, sa.ForeignKey('workflow_versions.id'), nullable=False),
        sa.Column('business_object_type', sa.String(50), nullable=False),
        sa.Column('business_object_id', uuid_pk, nullable=False),
        sa.Column('status', instance_status, nullable=False, server_default='running'),
        sa.Column('result', sa.Text, nullable=True),
        sa.Column('serialized_state', postgresql.JSONB, nullable=True),
        sa.Column('serializer_version', sa.String(10), nullable=True),
        sa.Column('started_by', uuid_pk, sa.ForeignKey('users.id'), nullable=False),
        sa.Column('started_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column('completed_at', sa.DateTime, nullable=True),
        sa.Column('error_detail', sa.Text, nullable=True),
    )
    op.create_index('ix_workflow_instances_business_object', 'workflow_instances', ['business_object_type', 'business_object_id'])

    op.create_table(
        'approval_rules',
        sa.Column('id', uuid_pk, primary_key=True),
        sa.Column('key', sa.String(100), nullable=False, unique=True),
        sa.Column('name', sa.String(200), nullable=False),
        sa.Column('definition_id', uuid_pk, sa.ForeignKey('workflow_definitions.id', ondelete='CASCADE'), nullable=True),
        sa.Column('step_key', sa.String(100), nullable=True),
        sa.Column('priority', sa.Integer, nullable=False, server_default='100'),
        sa.Column('is_active', sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column('stop_on_match', sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column('is_system', sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column('created_by', uuid_pk, sa.ForeignKey('users.id'), nullable=True),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        'rule_conditions',
        sa.Column('id', uuid_pk, primary_key=True),
        sa.Column('rule_id', uuid_pk, sa.ForeignKey('approval_rules.id', ondelete='CASCADE'), nullable=False),
        sa.Column('expression', sa.Text, nullable=False),
        sa.Column('description', sa.String(255), nullable=True),
        sa.Column('sequence', sa.Integer, nullable=False, server_default='0'),
    )

    op.create_table(
        'rule_actions',
        sa.Column('id', uuid_pk, primary_key=True),
        sa.Column('rule_id', uuid_pk, sa.ForeignKey('approval_rules.id', ondelete='CASCADE'), nullable=False),
        sa.Column('action_type', rule_action_type, nullable=False),
        sa.Column('strategy', approver_strategy, nullable=True),
        sa.Column('role_name', sa.String(100), nullable=True),
        sa.Column('user_ids', postgresql.JSONB, nullable=True),
        sa.Column('variable_name', sa.String(100), nullable=True),
        sa.Column('variable_value', postgresql.JSONB, nullable=True),
        sa.Column('config', postgresql.JSONB, nullable=True),
        sa.Column('sequence', sa.Integer, nullable=False, server_default='0'),
    )

    # workflow_tasks.rule_id references approval_rules, created after both
    # tables exist.
    op.create_table(
        'workflow_tasks',
        sa.Column('id', uuid_pk, primary_key=True),
        sa.Column('instance_id', uuid_pk, sa.ForeignKey('workflow_instances.id', ondelete='CASCADE'), nullable=False),
        sa.Column('spiff_task_id', sa.String(64), nullable=False),
        sa.Column('task_spec_name', sa.String(200), nullable=False),
        sa.Column('task_name', sa.String(200), nullable=True),
        sa.Column('step_key', sa.String(100), nullable=True),
        sa.Column('status', workflow_task_status, nullable=False, server_default='pending'),
        sa.Column('assigned_user_id', uuid_pk, sa.ForeignKey('users.id'), nullable=True),
        sa.Column('candidate_user_ids', postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column('rule_id', uuid_pk, sa.ForeignKey('approval_rules.id'), nullable=True),
        sa.Column('casbin_resource', sa.String(100), nullable=True),
        sa.Column('casbin_action', sa.String(100), nullable=True),
        sa.Column('due_at', sa.DateTime, nullable=True),
        sa.Column('acted_by', uuid_pk, sa.ForeignKey('users.id'), nullable=True),
        sa.Column('acted_at', sa.DateTime, nullable=True),
        sa.Column('comment', sa.Text, nullable=True),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index('ix_workflow_tasks_instance_id', 'workflow_tasks', ['instance_id'])

    op.create_table(
        'workflow_history',
        sa.Column('id', uuid_pk, primary_key=True),
        sa.Column('instance_id', uuid_pk, sa.ForeignKey('workflow_instances.id', ondelete='CASCADE'), nullable=False),
        sa.Column('task_id', uuid_pk, sa.ForeignKey('workflow_tasks.id', ondelete='SET NULL'), nullable=True),
        sa.Column('event_type', history_event_type, nullable=False),
        sa.Column('actor_user_id', uuid_pk, sa.ForeignKey('users.id'), nullable=True),
        sa.Column('actor_username', sa.String(100), nullable=True),
        sa.Column('from_status', sa.String(30), nullable=True),
        sa.Column('to_status', sa.String(30), nullable=True),
        sa.Column('comment', sa.Text, nullable=True),
        sa.Column('detail', postgresql.JSONB, nullable=True),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index('ix_workflow_history_instance_id', 'workflow_history', ['instance_id'])

    op.create_table(
        'workflow_variables',
        sa.Column('id', uuid_pk, primary_key=True),
        sa.Column('instance_id', uuid_pk, sa.ForeignKey('workflow_instances.id', ondelete='CASCADE'), nullable=False),
        sa.Column('name', sa.String(100), nullable=False),
        sa.Column('value', postgresql.JSONB, nullable=True),
        sa.Column('value_type', sa.String(20), nullable=False),
        sa.Column('is_input', sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column('updated_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column('updated_by', uuid_pk, sa.ForeignKey('users.id'), nullable=True),
        sa.UniqueConstraint('instance_id', 'name', name='uq_workflow_variable_instance_name'),
    )

    # ── Permission catalog + grants ────────────────────────────────────
    for resource, action, description in NEW_PERMISSIONS:
        _seed_permission(conn, resource, action, description)
    for resource, action in ADMIN_GRANTS:
        _grant(conn, "admin", resource, action)
    for resource, action in ENGINEER_GRANTS:
        _grant(conn, "engineer", resource, action)

    # ── Seed the three built-in gated workflows ────────────────────────
    admin_row = conn.execute(sa.text("SELECT id FROM users WHERE username = 'admin' LIMIT 1")).first()
    seed_user_id = str(admin_row[0]) if admin_row else None

    for key, (name, description, start_name, task_name) in SEEDED_WORKFLOWS.items():
        definition_id = str(uuid.uuid4())
        conn.execute(
            sa.text(
                "INSERT INTO workflow_definitions (id, key, name, description, is_active, is_system, created_by, created_at) "
                "VALUES (:id, :key, :name, :description, true, true, :created_by, now())"
            ),
            {"id": definition_id, "key": key, "name": name, "description": description, "created_by": seed_user_id},
        )

        version_id = str(uuid.uuid4())
        bpmn_xml = _BPMN_TEMPLATE.format(key=key, start_name=start_name, task_name=task_name)
        conn.execute(
            sa.text(
                "INSERT INTO workflow_versions "
                "(id, definition_id, version, bpmn_xml, process_id, status, notes, created_by, created_at, published_at, published_by) "
                "VALUES (:id, :definition_id, 1, :bpmn_xml, :process_id, 'published', :notes, :created_by, now(), now(), :created_by)"
            ),
            {
                "id": version_id, "definition_id": definition_id, "bpmn_xml": bpmn_xml, "process_id": key,
                "notes": "Seeded by migration 012.", "created_by": seed_user_id,
            },
        )

        rule_id = str(uuid.uuid4())
        conn.execute(
            sa.text(
                "INSERT INTO approval_rules "
                "(id, key, name, definition_id, step_key, priority, is_active, stop_on_match, is_system, created_by, created_at, updated_at) "
                "VALUES (:id, :rule_key, :rule_name, :definition_id, :step_key, 100, true, true, true, :created_by, now(), now())"
            ),
            {
                "id": rule_id, "rule_key": f"{key}_admin_approval", "rule_name": f"{name} — route to admin",
                "definition_id": definition_id, "step_key": f"{key}_approval", "created_by": seed_user_id,
            },
        )
        conn.execute(
            sa.text(
                "INSERT INTO rule_actions (id, rule_id, action_type, strategy, role_name, sequence) "
                "VALUES (:id, :rule_id, 'assign_approver', 'casbin_role', 'admin', 0)"
            ),
            {"id": str(uuid.uuid4()), "rule_id": rule_id},
        )


def downgrade() -> None:
    conn = op.get_bind()
    for resource, action, _ in NEW_PERMISSIONS:
        conn.execute(sa.text(
            "DELETE FROM casbin_rule WHERE ptype = 'p' AND v1 = :domain AND v2 = :resource AND v3 = :action"
        ), {"domain": DOMAIN, "resource": resource, "action": action})
        conn.execute(sa.text(
            "DELETE FROM permissions WHERE resource = :resource AND action = :action"
        ), {"resource": resource, "action": action})

    op.drop_table('workflow_variables')
    op.drop_index('ix_workflow_history_instance_id', table_name='workflow_history')
    op.drop_table('workflow_history')
    op.drop_index('ix_workflow_tasks_instance_id', table_name='workflow_tasks')
    op.drop_table('workflow_tasks')
    op.drop_table('rule_actions')
    op.drop_table('rule_conditions')
    op.drop_table('approval_rules')
    op.drop_index('ix_workflow_instances_business_object', table_name='workflow_instances')
    op.drop_table('workflow_instances')
    op.drop_index('uq_workflow_definition_published', table_name='workflow_versions')
    op.drop_table('workflow_versions')
    op.drop_table('workflow_definitions')

    op.execute("DROP TYPE IF EXISTS historyeventtype")
    op.execute("DROP TYPE IF EXISTS workflowtaskstatus")
    op.execute("DROP TYPE IF EXISTS instancestatus")
    op.execute("DROP TYPE IF EXISTS workflowversionstatus")
    op.execute("DROP TYPE IF EXISTS approverstrategy")
    op.execute("DROP TYPE IF EXISTS ruleactiontype")
