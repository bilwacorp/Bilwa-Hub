"""SQLAlchemy models for the business-rule engine that decides workflow
routing (who approves, is a step required). Ported from PoultryPro-CBF's
app/rules/models.py with the branch concept and the branch_manager /
linked_supervisor approver strategies stripped — this hub has no
branch/supervisor concept, only Casbin roles and explicit user lists (see
resolvers.py). See app/workflow/models.py for the BPMN side; the two
packages only reference each other by table name in ForeignKey strings,
never by importing each other's classes."""
import enum
import uuid
from datetime import datetime
from typing import List, Optional

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


# ── Enums ────────────────────────────────────────────────────────────────

class RuleActionType(str, enum.Enum):
    assign_approver = "assign_approver"
    auto_approve = "auto_approve"
    skip_step = "skip_step"
    set_variable = "set_variable"


class ApproverStrategy(str, enum.Enum):
    """Tried in this priority order by rules/resolvers.py when a rule
    action is `assign_approver` and doesn't pin a specific strategy: a
    named Casbin role first, then explicit user ids as a last resort for
    special cases (hardcoded user ids are exactly what this engine exists
    to avoid, so explicit_users should be rare)."""
    casbin_role = "casbin_role"
    explicit_users = "explicit_users"


# ── Models ───────────────────────────────────────────────────────────────

class ApprovalRule(Base):
    """One routing rule."""
    __tablename__ = "approval_rules"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    definition_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("workflow_definitions.id", ondelete="CASCADE"), nullable=True)
    step_key: Mapped[Optional[str]] = mapped_column(String(100))
    priority: Mapped[int] = mapped_column(Integer, default=100)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    stop_on_match: Mapped[bool] = mapped_column(Boolean, default=True)
    # True = migration-seeded; structural edits and deletion are blocked
    # via the API (the is_active toggle stays allowed). Mirrors
    # workflow_definitions.is_system and roles.is_system.
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    conditions: Mapped[List["RuleCondition"]] = relationship(
        "RuleCondition", back_populates="rule", cascade="all, delete-orphan",
        order_by="RuleCondition.sequence",
    )
    actions: Mapped[List["RuleAction"]] = relationship(
        "RuleAction", back_populates="rule", cascade="all, delete-orphan",
        order_by="RuleAction.sequence",
    )


class RuleCondition(Base):
    """One expression evaluated against the rule context. Conditions on the
    same rule are ANDed together; OR logic belongs inside a single
    expression (rule-engine supports `or` natively)."""
    __tablename__ = "rule_conditions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    rule_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("approval_rules.id", ondelete="CASCADE"), nullable=False)
    expression: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(255))
    sequence: Mapped[int] = mapped_column(Integer, default=0)

    rule: Mapped["ApprovalRule"] = relationship("ApprovalRule", back_populates="conditions")


class RuleAction(Base):
    """What happens when a rule's conditions all match. Only the fields
    relevant to `action_type` are populated; the rest stay NULL."""
    __tablename__ = "rule_actions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    rule_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("approval_rules.id", ondelete="CASCADE"), nullable=False)
    action_type: Mapped[RuleActionType] = mapped_column(Enum(RuleActionType), nullable=False)
    strategy: Mapped[Optional[ApproverStrategy]] = mapped_column(Enum(ApproverStrategy))
    role_name: Mapped[Optional[str]] = mapped_column(String(100))
    user_ids: Mapped[Optional[list]] = mapped_column(JSONB)
    variable_name: Mapped[Optional[str]] = mapped_column(String(100))
    variable_value: Mapped[Optional[dict]] = mapped_column(JSONB)
    config: Mapped[Optional[dict]] = mapped_column(JSONB)
    sequence: Mapped[int] = mapped_column(Integer, default=0)

    rule: Mapped["ApprovalRule"] = relationship("ApprovalRule", back_populates="actions")
