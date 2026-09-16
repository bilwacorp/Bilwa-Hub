"""Priority-ordered lookup of approval rules — the query at the heart of
"never hardcode an approver". Ported from PoultryPro-CBF's
app/rules/repositories.py with branch scoping stripped. See
rules/resolvers.py for what happens once a rule's actions are read,
rules/evaluator.py for how its conditions are evaluated.
"""
import uuid
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.rules.models import ApprovalRule


async def find_candidate_rules(
    db: AsyncSession, *, definition_id: uuid.UUID, step_key: Optional[str],
) -> List[ApprovalRule]:
    """Every active rule that could apply to this step, ordered so the
    caller (rules/services.find_first_matching_rule) can take them in
    order and stop at the first match:
      1. definition-specific before generic (applies to any workflow)
      2. step-specific before any-step
      3. priority ascending (lower number = evaluated first) within each tier
    A rule matches this step if its definition_id is NULL-or-this-definition
    and its step_key is NULL-or-this-step — NULL on either axis means "any".
    """
    q = (
        select(ApprovalRule)
        .options(selectinload(ApprovalRule.conditions), selectinload(ApprovalRule.actions))
        .where(
            ApprovalRule.is_active.is_(True),
            (ApprovalRule.definition_id == definition_id) | (ApprovalRule.definition_id.is_(None)),
            (ApprovalRule.step_key == step_key) | (ApprovalRule.step_key.is_(None)),
        )
        .order_by(
            ApprovalRule.definition_id.is_(None).asc(),
            ApprovalRule.step_key.is_(None).asc(),
            ApprovalRule.priority.asc(),
        )
    )
    result = await db.execute(q)
    return list(result.scalars().unique().all())
