"""Approval-rule CRUD, the rule tester, and expression validation. Rule
resolution used by live workflow execution lives in rules/services.py and
is called from app/workflow/executor.py — this router is the admin-facing
config surface on top of the same service functions. Ported from
PoultryPro-CBF's app/rules/api.py with branch context stripped."""
import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.core.permissions import (
    require_permission,
    WORKFLOW_RULES_VIEW, WORKFLOW_RULES_CREATE, WORKFLOW_RULES_UPDATE, WORKFLOW_RULES_DELETE, WORKFLOW_RULES_TEST,
)
from app.models import User
from app.rules import resolvers, services
from app.rules.evaluator import validate_expression
from app.rules.models import RuleActionType
from app.rules.schemas import (
    ApprovalRuleCreate, ApprovalRuleOut, ApprovalRuleUpdate,
    ExpressionValidateRequest, ExpressionValidateResult, RuleTestRequest, RuleTestResult,
)

router = APIRouter(prefix="/workflow-rules", tags=["workflow-rules"])

# The base keys the executor always injects into a rule's evaluation
# context (see app/workflow/executor.py's _build_rule_context) — surfaced
# here so the rule-editor UI can autocomplete/document them. Anything else
# is a per-instance variable and varies by which action started it.
_BASE_CONTEXT_KEYS = [
    {"name": "business_object_type", "type": "string", "description": "e.g. 'deployment_renew', 'deployment_suspend', 'deployment_change_plan'."},
    {"name": "business_object_id", "type": "string", "description": "UUID of the business object this instance is running for (the deployment id)."},
    {"name": "started_by", "type": "string", "description": "UUID of the user who started the instance."},
    {"name": "step_key", "type": "string | null", "description": "The current BPMN step's stepKey property."},
]


@router.get("/context-schema")
async def get_context_schema(current_user: User = Depends(require_permission(*WORKFLOW_RULES_VIEW))):
    return {"base_keys": _BASE_CONTEXT_KEYS}


@router.post("/validate-expression", response_model=ExpressionValidateResult)
async def validate_rule_expression(
    body: ExpressionValidateRequest,
    current_user: User = Depends(require_permission(*WORKFLOW_RULES_VIEW)),
):
    error = validate_expression(body.expression)
    return ExpressionValidateResult(is_valid=error is None, error=error)


@router.get("", response_model=List[ApprovalRuleOut])
async def list_rules(
    definition_id: Optional[uuid.UUID] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(*WORKFLOW_RULES_VIEW)),
):
    return await services.list_rules(db, definition_id=definition_id)


@router.post("", response_model=ApprovalRuleOut, status_code=201)
async def create_rule(
    body: ApprovalRuleCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(*WORKFLOW_RULES_CREATE)),
):
    return await services.create_rule(db, current_user, body)


@router.get("/{rule_id}", response_model=ApprovalRuleOut)
async def get_rule(
    rule_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(*WORKFLOW_RULES_VIEW)),
):
    return await services.get_rule(db, rule_id)


@router.patch("/{rule_id}", response_model=ApprovalRuleOut)
async def update_rule(
    rule_id: uuid.UUID,
    body: ApprovalRuleUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(*WORKFLOW_RULES_UPDATE)),
):
    return await services.update_rule(db, rule_id, body)


@router.delete("/{rule_id}", status_code=204)
async def delete_rule(
    rule_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(*WORKFLOW_RULES_DELETE)),
):
    await services.delete_rule(db, rule_id)


@router.post("/{rule_id}/test", response_model=RuleTestResult)
async def test_rule(
    rule_id: uuid.UUID,
    body: RuleTestRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(*WORKFLOW_RULES_TEST)),
):
    rule = await services.get_rule(db, rule_id)
    matched, error = services.evaluate_rule_conditions(rule, body.context)
    if error or not matched:
        return RuleTestResult(matched=False, error=error)

    resolved_ids: List[uuid.UUID] = []
    for action in rule.actions:
        if action.action_type == RuleActionType.assign_approver:
            business_object_type = body.context.get("business_object_type", "")
            business_object_id_raw = body.context.get("business_object_id")
            business_object_id = uuid.UUID(business_object_id_raw) if business_object_id_raw else uuid.uuid4()
            ids = await resolvers.resolve(
                db, action, business_object_type=business_object_type, business_object_id=business_object_id,
            )
            resolved_ids.extend(ids)

    return RuleTestResult(matched=True, resolved_approver_ids=resolved_ids)
