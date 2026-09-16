"""Rule CRUD, plus the rule resolution the workflow executor calls, and the
"test a rule against sample input" support for the admin UI. Ported from
PoultryPro-CBF's app/rules/services.py with branch scoping stripped, and
the is_system bypass dropped: this hub has no hidden bypass role (CBF's
bilwacorp_engineer / workflows.manage_system), so a built-in rule that has
already routed at least once is simply immutable via the API for
everyone — same as app/workflow/services.py's _assert_definition_mutable.
"""
import uuid
from typing import List, Optional, Tuple

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import User
from app.rules import repositories
from app.rules.evaluator import evaluate_safe, validate_expression
from app.rules.models import ApprovalRule, RuleAction, RuleCondition
from app.rules.schemas import ApprovalRuleCreate, ApprovalRuleUpdate, RuleConditionIn

_LOCKED = "This is a built-in rule — only its active/inactive state can be changed."


def _validate_conditions(conditions: List[RuleConditionIn]) -> None:
    errors = []
    for c in conditions:
        err = validate_expression(c.expression)
        if err:
            errors.append(f"'{c.expression}': {err}")
    if errors:
        raise HTTPException(status_code=400, detail="; ".join(errors))


async def list_rules(db: AsyncSession, *, definition_id: Optional[uuid.UUID] = None) -> List[ApprovalRule]:
    q = (
        select(ApprovalRule)
        .options(selectinload(ApprovalRule.conditions), selectinload(ApprovalRule.actions))
        .order_by(ApprovalRule.priority)
    )
    if definition_id is not None:
        q = q.where(ApprovalRule.definition_id == definition_id)
    result = await db.execute(q)
    return list(result.scalars().unique().all())


async def get_rule(db: AsyncSession, rule_id: uuid.UUID) -> ApprovalRule:
    result = await db.execute(
        select(ApprovalRule)
        .options(selectinload(ApprovalRule.conditions), selectinload(ApprovalRule.actions))
        .where(ApprovalRule.id == rule_id)
    )
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    return rule


def _add_actions(db: AsyncSession, rule_id: uuid.UUID, actions) -> None:
    for i, a in enumerate(actions):
        db.add(RuleAction(
            rule_id=rule_id, action_type=a.action_type, strategy=a.strategy, role_name=a.role_name,
            user_ids=[str(u) for u in a.user_ids] if a.user_ids else None,
            variable_name=a.variable_name, variable_value=a.variable_value, config=a.config, sequence=i,
        ))


async def create_rule(db: AsyncSession, current_user: User, body: ApprovalRuleCreate) -> ApprovalRule:
    _validate_conditions(body.conditions)
    rule = ApprovalRule(
        key=body.key, name=body.name, definition_id=body.definition_id, step_key=body.step_key,
        priority=body.priority, is_active=body.is_active, stop_on_match=body.stop_on_match,
        is_system=body.is_system, created_by=current_user.id,
    )
    db.add(rule)
    await db.flush()
    for i, c in enumerate(body.conditions):
        db.add(RuleCondition(rule_id=rule.id, expression=c.expression, description=c.description, sequence=i))
    _add_actions(db, rule.id, body.actions)
    await db.flush()
    return await get_rule(db, rule.id)


async def update_rule(db: AsyncSession, rule_id: uuid.UUID, body: ApprovalRuleUpdate) -> ApprovalRule:
    rule = await get_rule(db, rule_id)
    if rule.is_system:
        # Built-in rules: only the is_active toggle is allowed; everything
        # else is frozen so the seeded routing can't be silently rewritten.
        changed = body.model_dump(exclude_unset=True)
        changed.pop("is_active", None)
        if changed:
            raise HTTPException(status_code=403, detail=_LOCKED)
    if body.conditions is not None:
        _validate_conditions(body.conditions)

    if body.name is not None:
        rule.name = body.name
    if body.step_key is not None:
        rule.step_key = body.step_key
    if body.priority is not None:
        rule.priority = body.priority
    if body.is_active is not None:
        rule.is_active = body.is_active
    if body.stop_on_match is not None:
        rule.stop_on_match = body.stop_on_match

    if body.conditions is not None:
        for c in list(rule.conditions):
            await db.delete(c)
        await db.flush()
        for i, c in enumerate(body.conditions):
            db.add(RuleCondition(rule_id=rule.id, expression=c.expression, description=c.description, sequence=i))

    if body.actions is not None:
        for a in list(rule.actions):
            await db.delete(a)
        await db.flush()
        _add_actions(db, rule.id, body.actions)

    await db.flush()
    return await get_rule(db, rule.id)


async def delete_rule(db: AsyncSession, rule_id: uuid.UUID) -> None:
    """A rule that has already routed a task (workflow_tasks.rule_id, no
    ondelete on that FK — task history must keep pointing at the rule that
    actually made the decision) can't be deleted outright; deactivate it
    instead."""
    rule = await get_rule(db, rule_id)
    if rule.is_system:
        raise HTTPException(status_code=403, detail="Cannot delete a built-in rule — deactivate it instead.")
    try:
        await db.delete(rule)
        await db.flush()
    except IntegrityError:
        raise HTTPException(
            status_code=409,
            detail="This rule has already routed at least one task and can't be deleted — deactivate it instead.",
        )


def evaluate_rule_conditions(rule: ApprovalRule, context: dict) -> Tuple[bool, Optional[str]]:
    """AND every condition (no conditions at all = always matches);
    short-circuits on the first non-match, surfaces the first error."""
    for cond in sorted(rule.conditions, key=lambda c: c.sequence):
        matched, error = evaluate_safe(cond.expression, context)
        if error:
            return False, error
        if not matched:
            return False, None
    return True, None


async def find_first_matching_rule(
    db: AsyncSession, *, definition_id: uuid.UUID, step_key: Optional[str], context: dict
) -> Tuple[Optional[ApprovalRule], Optional[str]]:
    """The "which rule applies to this step" lookup the workflow executor
    calls. Returns (rule, error) — error is set (rule is None) if a
    candidate rule's own condition raised, so the caller records a
    rule_error history entry instead of silently falling through to
    whichever rule happens to be next in priority."""
    candidates = await repositories.find_candidate_rules(db, definition_id=definition_id, step_key=step_key)
    for rule in candidates:
        matched, error = evaluate_rule_conditions(rule, context)
        if error:
            return None, f"Rule '{rule.key}': {error}"
        if matched:
            return rule, None
    return None, None
