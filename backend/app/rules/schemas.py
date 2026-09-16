"""Pydantic v2 schemas for the rules package. House style: XCreate/XUpdate
request bodies with no model_config; XOut response schemas with
model_config = {"from_attributes": True} placed after the fields. Ported
from PoultryPro-CBF's app/rules/schemas.py with branch_id/
restrict_to_instance_branch stripped."""
import uuid
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field

from app.rules.models import ApproverStrategy, RuleActionType


class RuleConditionIn(BaseModel):
    expression: str
    description: Optional[str] = None


class RuleConditionOut(BaseModel):
    id: uuid.UUID
    expression: str
    description: Optional[str]
    sequence: int

    model_config = {"from_attributes": True}


class RuleActionIn(BaseModel):
    action_type: RuleActionType
    strategy: Optional[ApproverStrategy] = None
    role_name: Optional[str] = None
    user_ids: Optional[List[uuid.UUID]] = None
    variable_name: Optional[str] = None
    variable_value: Optional[object] = None
    config: Optional[dict] = None


class RuleActionOut(BaseModel):
    id: uuid.UUID
    action_type: RuleActionType
    strategy: Optional[ApproverStrategy]
    role_name: Optional[str]
    user_ids: Optional[List[uuid.UUID]]
    variable_name: Optional[str]
    variable_value: Optional[object]
    config: Optional[dict]
    sequence: int

    model_config = {"from_attributes": True}


class ApprovalRuleCreate(BaseModel):
    key: str
    name: str
    definition_id: Optional[uuid.UUID] = None
    step_key: Optional[str] = None
    priority: int = 100
    is_active: bool = True
    stop_on_match: bool = True
    # Set only by the migration seed; the UI never sends it.
    is_system: bool = False
    conditions: List[RuleConditionIn] = Field(default_factory=list)
    actions: List[RuleActionIn] = Field(min_length=1)


class ApprovalRuleUpdate(BaseModel):
    name: Optional[str] = None
    step_key: Optional[str] = None
    priority: Optional[int] = None
    is_active: Optional[bool] = None
    stop_on_match: Optional[bool] = None
    conditions: Optional[List[RuleConditionIn]] = None
    actions: Optional[List[RuleActionIn]] = None


class ApprovalRuleOut(BaseModel):
    id: uuid.UUID
    key: str
    name: str
    definition_id: Optional[uuid.UUID]
    step_key: Optional[str]
    priority: int
    is_active: bool
    stop_on_match: bool
    is_system: bool
    created_by: Optional[uuid.UUID]
    created_at: datetime
    updated_at: datetime
    conditions: List[RuleConditionOut] = []
    actions: List[RuleActionOut] = []

    model_config = {"from_attributes": True}


class RuleTestRequest(BaseModel):
    context: dict


class RuleTestResult(BaseModel):
    matched: bool
    error: Optional[str] = None
    resolved_approver_ids: List[uuid.UUID] = []


class ExpressionValidateRequest(BaseModel):
    expression: str


class ExpressionValidateResult(BaseModel):
    is_valid: bool
    error: Optional[str] = None
