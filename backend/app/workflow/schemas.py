"""Pydantic v2 schemas for the workflow package. House style: XCreate/XUpdate
request bodies with no model_config; XOut response schemas with
model_config = {"from_attributes": True} placed after the fields. Ported
from PoultryPro-CBF's app/workflow/schemas.py with branch_id stripped.
"""
import uuid
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field

from app.workflow.models import HistoryEventType, InstanceStatus, WorkflowTaskStatus, WorkflowVersionStatus


# ── Definitions / versions ──────────────────────────────────────────────

class WorkflowDefinitionCreate(BaseModel):
    key: str
    name: str
    description: Optional[str] = None
    # Only the migration seed sets this; the UI never sends it, so
    # UI-created workflows are always custom (is_system=False).
    is_system: bool = False


class WorkflowDefinitionUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None


class WorkflowDefinitionOut(BaseModel):
    id: uuid.UUID
    key: str
    name: str
    description: Optional[str]
    is_active: bool
    is_system: bool
    created_by: Optional[uuid.UUID]
    created_at: datetime

    model_config = {"from_attributes": True}


class WorkflowVersionCreate(BaseModel):
    bpmn_xml: str
    process_id: str
    notes: Optional[str] = None


class WorkflowVersionOut(BaseModel):
    id: uuid.UUID
    definition_id: uuid.UUID
    version: int
    bpmn_xml: str
    process_id: str
    status: WorkflowVersionStatus
    notes: Optional[str]
    created_by: Optional[uuid.UUID]
    created_at: datetime
    published_at: Optional[datetime]
    published_by: Optional[uuid.UUID]

    model_config = {"from_attributes": True}


class WorkflowVersionListOut(BaseModel):
    """Version list rows omit bpmn_xml — it can be tens of KB and the list
    view never renders it; GET .../versions/{id} returns the full body."""
    id: uuid.UUID
    definition_id: uuid.UUID
    version: int
    process_id: str
    status: WorkflowVersionStatus
    notes: Optional[str]
    created_by: Optional[uuid.UUID]
    created_at: datetime
    published_at: Optional[datetime]

    model_config = {"from_attributes": True}


class ValidationStepOut(BaseModel):
    task_id: str
    name: Optional[str]
    step_key: Optional[str]
    rule_key: Optional[str]


class ValidationResultOut(BaseModel):
    is_valid: bool
    errors: List[str]
    steps: List[ValidationStepOut]


class ImportBpmnRequest(BaseModel):
    bpmn_xml: str


class ImportBpmnResult(BaseModel):
    process_ids: List[str]


# ── Instances ────────────────────────────────────────────────────────────

class WorkflowInstanceOut(BaseModel):
    id: uuid.UUID
    instance_code: str
    definition_id: uuid.UUID
    version_id: uuid.UUID
    business_object_type: str
    business_object_id: uuid.UUID
    status: InstanceStatus
    result: Optional[str]
    started_by: uuid.UUID
    started_at: datetime
    completed_at: Optional[datetime]
    error_detail: Optional[str]

    model_config = {"from_attributes": True}


class WorkflowVariableOut(BaseModel):
    id: uuid.UUID
    name: str
    value: Optional[object]
    value_type: str
    is_input: bool
    updated_at: datetime
    updated_by: Optional[uuid.UUID]

    model_config = {"from_attributes": True}


class WorkflowVariableUpdate(BaseModel):
    name: str
    value: object


class WorkflowHistoryOut(BaseModel):
    id: uuid.UUID
    instance_id: uuid.UUID
    task_id: Optional[uuid.UUID]
    event_type: HistoryEventType
    actor_user_id: Optional[uuid.UUID]
    actor_username: Optional[str]
    from_status: Optional[str]
    to_status: Optional[str]
    comment: Optional[str]
    detail: Optional[dict]
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Tasks / approvals ────────────────────────────────────────────────────

class WorkflowTaskOut(BaseModel):
    id: uuid.UUID
    instance_id: uuid.UUID
    task_spec_name: str
    task_name: Optional[str]
    step_key: Optional[str]
    status: WorkflowTaskStatus
    assigned_user_id: Optional[uuid.UUID]
    candidate_user_ids: List[uuid.UUID]
    rule_id: Optional[uuid.UUID]
    due_at: Optional[datetime]
    acted_by: Optional[uuid.UUID]
    acted_at: Optional[datetime]
    comment: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}


class TaskActionRequest(BaseModel):
    comment: Optional[str] = None
    data: dict = Field(default_factory=dict)


class TaskReassignRequest(BaseModel):
    user_id: uuid.UUID
    comment: Optional[str] = None
