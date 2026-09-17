"""HUB-Expansion.md Phase 7 — Maintenance Lifecycle + Phase 8's expanded
use of the approval engine for maintenance windows. See
docs/adr/ADR-007-maintenance-lifecycle-and-approvals.md for the full
design.

Widens `maintenancewindowstatus` from 4 to 9 values (draft/
approval_required/approved/notification/failed are new; planned/
in_progress/completed/cancelled are unchanged, NOT renamed — see the
ADR's decision 1). Adds `maintenance_windows.expected_impact`/
`actual_impact`/`approved_by`. Seeds a fourth built-in gated workflow,
`maintenance_window_approval` — same BPMN-template shape as migration
012's three deployment-action workflows (one user task, routed to
'admin'), copied rather than imported since each migration is meant to
stand alone as a snapshot even if the application code that originally
generated it changes later.

No new permission — app/approvals/maintenance_hooks.py's route_window is
reached from the existing maintenance.create action (create + submit),
same as how deployment_hooks.py's gate needed no separate permission
beyond deployments.renew/suspend/change_plan.

Revision ID: 018_maintenance_lifecycle
Revises: 017_ticket_links
Create Date: 2026-09-17
"""
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '018_maintenance_lifecycle'
down_revision: Union[str, None] = '017_ticket_links'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NEW_STATUS_VALUES = ["draft", "approval_required", "approved", "notification", "failed"]

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

KEY = "maintenance_window_approval"
NAME = "Maintenance Window Approval"
DESCRIPTION = "Requires approval before a high-risk (fleet-wide or lockout) maintenance window is scheduled."
START_NAME = "Maintenance Submitted"
TASK_NAME = "Approve Maintenance"


def upgrade() -> None:
    conn = op.get_bind()

    for value in _NEW_STATUS_VALUES:
        op.execute(f"ALTER TYPE maintenancewindowstatus ADD VALUE IF NOT EXISTS '{value}'")

    op.add_column('maintenance_windows', sa.Column('expected_impact', sa.Text, nullable=True))
    op.add_column('maintenance_windows', sa.Column('actual_impact', sa.Text, nullable=True))
    op.add_column('maintenance_windows', sa.Column('approved_by', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id'), nullable=True))

    admin_row = conn.execute(sa.text("SELECT id FROM users WHERE username = 'admin' LIMIT 1")).first()
    seed_user_id = str(admin_row[0]) if admin_row else None

    definition_id = str(uuid.uuid4())
    conn.execute(
        sa.text(
            "INSERT INTO workflow_definitions (id, key, name, description, is_active, is_system, created_by, created_at) "
            "VALUES (:id, :key, :name, :description, true, true, :created_by, now())"
        ),
        {"id": definition_id, "key": KEY, "name": NAME, "description": DESCRIPTION, "created_by": seed_user_id},
    )

    version_id = str(uuid.uuid4())
    bpmn_xml = _BPMN_TEMPLATE.format(key=KEY, start_name=START_NAME, task_name=TASK_NAME)
    conn.execute(
        sa.text(
            "INSERT INTO workflow_versions "
            "(id, definition_id, version, bpmn_xml, process_id, status, notes, created_by, created_at, published_at, published_by) "
            "VALUES (:id, :definition_id, 1, :bpmn_xml, :process_id, 'published', :notes, :created_by, now(), now(), :created_by)"
        ),
        {
            "id": version_id, "definition_id": definition_id, "bpmn_xml": bpmn_xml, "process_id": KEY,
            "notes": "Seeded by migration 018.", "created_by": seed_user_id,
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
            "id": rule_id, "rule_key": f"{KEY}_admin_approval", "rule_name": f"{NAME} — route to admin",
            "definition_id": definition_id, "step_key": f"{KEY}_approval", "created_by": seed_user_id,
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
    definition_row = conn.execute(sa.text("SELECT id FROM workflow_definitions WHERE key = :key"), {"key": KEY}).first()
    if definition_row:
        definition_id = definition_row[0]
        conn.execute(sa.text(
            "DELETE FROM rule_actions WHERE rule_id IN (SELECT id FROM approval_rules WHERE definition_id = :did)"
        ), {"did": definition_id})
        conn.execute(sa.text("DELETE FROM approval_rules WHERE definition_id = :did"), {"did": definition_id})
        conn.execute(sa.text("DELETE FROM workflow_versions WHERE definition_id = :did"), {"did": definition_id})
        conn.execute(sa.text("DELETE FROM workflow_definitions WHERE id = :did"), {"did": definition_id})

    op.drop_column('maintenance_windows', 'approved_by')
    op.drop_column('maintenance_windows', 'actual_impact')
    op.drop_column('maintenance_windows', 'expected_impact')

    # Postgres has no "DROP VALUE" for enums — the new values are left in
    # place on downgrade (harmless: nothing references them once the
    # columns/seed rows above are gone). Matches this repo's existing
    # precedent of not attempting enum-value removal on downgrade.
