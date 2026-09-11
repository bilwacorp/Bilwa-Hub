"""All hub models in one file — mirrors PoultryOS-CBP's single
models/models.py convention. Phase 1 scope only (see the plan): staff auth,
the deployment registry + heartbeat snapshots, support tickets, maintenance
windows."""
import enum
import uuid
from datetime import date, datetime
from typing import Optional

from sqlalchemy import JSON, Boolean, Date, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class User(Base):
    """BilwaCorp staff login. Phase 1 has exactly one seeded row (see
    alembic/versions/002_seed_admin.py) — no self-signup, no user-management
    UI yet."""
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    username: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    full_name: Mapped[Optional[str]] = mapped_column(String(100))
    email: Mapped[Optional[str]] = mapped_column(String(120), unique=True)
    hashed_password: Mapped[str] = mapped_column(String(128), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Bumped on password change to invalidate every JWT issued before that
    # point — see core/deps.py's get_current_user, ported from
    # PoultryOS-CBP's same convention.
    token_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class DeploymentStatus(str, enum.Enum):
    pending = "pending"       # row created, registration_token not yet consumed
    active = "active"         # registered, at least one heartbeat expected
    suspended = "suspended"   # BilwaCorp-side flag, not currently enforced by any gate


class Deployment(Base):
    """One row per client PoultryOS-CBP instance. See the plan's "Two
    credentials, not one" section for why api_key is hash-only but
    action_key is a reversible (Fernet) encrypted column — the hub must be
    able to present action_key back to the deployment when calling in to
    run an action, but never needs to present api_key (it only verifies
    that direction)."""
    __tablename__ = "deployments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    client_name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    base_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    status: Mapped[DeploymentStatus] = mapped_column(Enum(DeploymentStatus), default=DeploymentStatus.pending, nullable=False)

    registration_token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    registration_token_consumed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    api_key_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    action_key_encrypted: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    snapshots: Mapped[list["DeploymentSnapshot"]] = relationship(
        "DeploymentSnapshot", back_populates="deployment", order_by="DeploymentSnapshot.received_at.desc()",
    )


class DeploymentSnapshot(Base):
    """One row per heartbeat received — see the plan's registration+
    heartbeat flow. received_at is always set server-side (never trusts the
    client's clock)."""
    __tablename__ = "deployment_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    deployment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("deployments.id"), nullable=False)
    app_version: Mapped[Optional[str]] = mapped_column(String(50))
    plan_name: Mapped[Optional[str]] = mapped_column(String(100))
    subscription_status: Mapped[Optional[str]] = mapped_column(String(50))
    expiry_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    trial_ends_at: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    auto_renew: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    usage: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    pending_requests: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    deployment: Mapped["Deployment"] = relationship("Deployment", back_populates="snapshots")


class SupportTicketStatus(str, enum.Enum):
    open = "open"
    in_progress = "in_progress"
    resolved = "resolved"
    closed = "closed"


class SupportTicket(Base):
    __tablename__ = "support_tickets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    deployment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("deployments.id"), nullable=False)
    subject: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[str] = mapped_column(String(20), default="normal", nullable=False)
    status: Mapped[SupportTicketStatus] = mapped_column(Enum(SupportTicketStatus), default=SupportTicketStatus.open, nullable=False)
    submitted_by_name: Mapped[Optional[str]] = mapped_column(String(200))
    submitted_by_email: Mapped[Optional[str]] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    deployment: Mapped["Deployment"] = relationship("Deployment")


class MaintenanceWindowStatus(str, enum.Enum):
    planned = "planned"
    in_progress = "in_progress"
    completed = "completed"
    cancelled = "cancelled"


class MaintenanceWindow(Base):
    """A scheduled maintenance window. `deployment_id` NULL = fleet-wide.
    The maintenance_scheduler loop auto-transitions status
    (planned -> in_progress -> completed) off scheduled_start/scheduled_end
    and pushes changes to affected deployments (services/deployment_client
    .push_maintenance) so their in-app banner / read-only gate track it.
    All datetimes are naive UTC, like everything else here — schemas that
    expose them append an explicit offset (see MaintenanceWindowPublic)."""
    __tablename__ = "maintenance_windows"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    deployment_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("deployments.id"), nullable=True)
    scheduled_start: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    scheduled_end: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[MaintenanceWindowStatus] = mapped_column(Enum(MaintenanceWindowStatus), default=MaintenanceWindowStatus.planned, nullable=False)
    # How hard the window bites while it's active, opt-in and escalating:
    #   "banner"    — just an in-app notice, nothing blocked (default).
    #   "read_only" — the deployment returns 503 for mutating requests
    #                 (its maintenance_gate_middleware); viewing still works.
    #   "lockout"   — read_only PLUS new sign-ins are refused; existing
    #                 sessions keep working (read-only) until they expire.
    # Stored as a plain string, not a DB enum, so adding a level later is a
    # code change only.
    mode: Mapped[str] = mapped_column(String(16), default="banner", nullable=False)
    # Per-deployment push bookkeeping so a fleet-wide window can be
    # pushed/retried to each deployment independently. Shape:
    #   { "<deployment_uuid>": {
    #       "notice_at": iso|null, "reminder_at": iso|null,
    #       "last_status": "in_progress"|null, "status_pushed_at": iso|null } }
    push_state: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    deployment: Mapped[Optional["Deployment"]] = relationship("Deployment")
