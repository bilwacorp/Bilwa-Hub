"""SQLAlchemy models for the embedded BPMN workflow engine — ported from
PoultryPro-CBF's app/workflow/models.py with the branch concept stripped
(this hub is single-org, not multi-branch) and is_system simplified: a
built-in (is_system) definition/rule is immutable via the API for
*everyone* once it has a published version, not just customer admins —
this hub has no hidden bypass role (CBF's bilwacorp_engineer /
workflows.manage_system) to carve an exception for. Table/column
conventions mirror app/models.py (UUID PKs, Mapped[...]/mapped_column(...),
str-enum + SQLAlchemy Enum)."""
import enum
import uuid
from datetime import datetime
from typing import List, Optional

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


# ── Enums ────────────────────────────────────────────────────────────────

class WorkflowVersionStatus(str, enum.Enum):
    draft = "draft"
    published = "published"
    archived = "archived"


class InstanceStatus(str, enum.Enum):
    running = "running"
    completed = "completed"
    rejected = "rejected"
    cancelled = "cancelled"
    error = "error"


class WorkflowTaskStatus(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"
    cancelled = "cancelled"
    skipped = "skipped"


class HistoryEventType(str, enum.Enum):
    instance_started = "instance_started"
    task_created = "task_created"
    task_approved = "task_approved"
    task_rejected = "task_rejected"
    task_auto_approved = "task_auto_approved"
    task_skipped = "task_skipped"
    task_reassigned = "task_reassigned"
    task_cancelled = "task_cancelled"
    instance_completed = "instance_completed"
    instance_rejected = "instance_rejected"
    instance_cancelled = "instance_cancelled"
    instance_error = "instance_error"
    rule_error = "rule_error"
    variable_updated = "variable_updated"


# ── Models ───────────────────────────────────────────────────────────────

class WorkflowDefinition(Base):
    """A named process (e.g. 'deployment_renew')."""
    __tablename__ = "workflow_definitions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(default=True)
    # True = migration-seeded (see alembic/versions/012_workflow_engine.py).
    # The API blocks structural edits/deletion of these once a version is
    # published (the is_active toggle stays allowed) so the seeded
    # deployment-action approval flow can't be broken by hand.
    is_system: Mapped[bool] = mapped_column(default=False, server_default="false")
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    versions: Mapped[List["WorkflowVersion"]] = relationship(
        "WorkflowVersion", back_populates="definition", cascade="all, delete-orphan",
        order_by="WorkflowVersion.version",
    )


class WorkflowVersion(Base):
    """One immutable BPMN revision of a definition."""
    __tablename__ = "workflow_versions"
    __table_args__ = (UniqueConstraint("definition_id", "version", name="uq_workflow_version_number"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    definition_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("workflow_definitions.id", ondelete="CASCADE"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    bpmn_xml: Mapped[str] = mapped_column(Text, nullable=False)
    process_id: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[WorkflowVersionStatus] = mapped_column(Enum(WorkflowVersionStatus), default=WorkflowVersionStatus.draft)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    published_by: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))

    definition: Mapped["WorkflowDefinition"] = relationship("WorkflowDefinition", back_populates="versions")


class WorkflowInstance(Base):
    """One running (or finished) execution of a workflow version against a
    specific business object — e.g. ("deployment_renew", <deployment.id>).
    A distinct business_object_type per gated action (rather than one
    shared "deployment" type) means a pending renew request never blocks a
    concurrent suspend request on the same deployment — see
    repositories.get_instance_for_object's "already running" check."""
    __tablename__ = "workflow_instances"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    instance_code: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    definition_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("workflow_definitions.id"), nullable=False)
    version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("workflow_versions.id"), nullable=False)
    business_object_type: Mapped[str] = mapped_column(String(50), nullable=False)
    business_object_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    status: Mapped[InstanceStatus] = mapped_column(Enum(InstanceStatus), default=InstanceStatus.running)
    result: Mapped[Optional[str]] = mapped_column(Text)
    serialized_state: Mapped[Optional[dict]] = mapped_column(JSONB)
    serializer_version: Mapped[Optional[str]] = mapped_column(String(10))
    started_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    error_detail: Mapped[Optional[str]] = mapped_column(Text)

    definition: Mapped["WorkflowDefinition"] = relationship("WorkflowDefinition")
    version: Mapped["WorkflowVersion"] = relationship("WorkflowVersion")
    tasks: Mapped[List["WorkflowTask"]] = relationship(
        "WorkflowTask", back_populates="instance", cascade="all, delete-orphan",
        order_by="WorkflowTask.created_at",
    )
    history: Mapped[List["WorkflowHistory"]] = relationship(
        "WorkflowHistory", back_populates="instance", cascade="all, delete-orphan",
        order_by="WorkflowHistory.created_at",
    )
    variables: Mapped[List["WorkflowVariable"]] = relationship(
        "WorkflowVariable", back_populates="instance", cascade="all, delete-orphan",
    )


class WorkflowTask(Base):
    """A human (manual/user) task raised by the engine."""
    __tablename__ = "workflow_tasks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    instance_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("workflow_instances.id", ondelete="CASCADE"), nullable=False)
    spiff_task_id: Mapped[str] = mapped_column(String(64), nullable=False)
    task_spec_name: Mapped[str] = mapped_column(String(200), nullable=False)
    task_name: Mapped[Optional[str]] = mapped_column(String(200))
    step_key: Mapped[Optional[str]] = mapped_column(String(100))
    status: Mapped[WorkflowTaskStatus] = mapped_column(Enum(WorkflowTaskStatus), default=WorkflowTaskStatus.pending)
    assigned_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    candidate_user_ids: Mapped[list] = mapped_column(JSONB, default=list)
    rule_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("approval_rules.id"))
    casbin_resource: Mapped[Optional[str]] = mapped_column(String(100))
    casbin_action: Mapped[Optional[str]] = mapped_column(String(100))
    due_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    acted_by: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    acted_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    comment: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    instance: Mapped["WorkflowInstance"] = relationship("WorkflowInstance", back_populates="tasks")


class WorkflowHistory(Base):
    """Append-only audit trail for an instance."""
    __tablename__ = "workflow_history"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    instance_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("workflow_instances.id", ondelete="CASCADE"), nullable=False)
    task_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("workflow_tasks.id", ondelete="SET NULL"))
    event_type: Mapped[HistoryEventType] = mapped_column(Enum(HistoryEventType), nullable=False)
    actor_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    actor_username: Mapped[Optional[str]] = mapped_column(String(100))
    from_status: Mapped[Optional[str]] = mapped_column(String(30))
    to_status: Mapped[Optional[str]] = mapped_column(String(30))
    comment: Mapped[Optional[str]] = mapped_column(Text)
    detail: Mapped[Optional[dict]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    instance: Mapped["WorkflowInstance"] = relationship("WorkflowInstance", back_populates="history")


class WorkflowVariable(Base):
    """Named input/derived values for an instance."""
    __tablename__ = "workflow_variables"
    __table_args__ = (UniqueConstraint("instance_id", "name", name="uq_workflow_variable_instance_name"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    instance_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("workflow_instances.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    value: Mapped[Optional[dict]] = mapped_column(JSONB)
    value_type: Mapped[str] = mapped_column(String(20), nullable=False)
    is_input: Mapped[bool] = mapped_column(default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))

    instance: Mapped["WorkflowInstance"] = relationship("WorkflowInstance", back_populates="variables")
