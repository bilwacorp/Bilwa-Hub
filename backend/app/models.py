"""All hub models in one file — mirrors PoultryOS-CBP's single
models/models.py convention. Phase 1 scope only (see the plan): staff auth,
the deployment registry + heartbeat snapshots, support tickets, maintenance
windows."""
import enum
import uuid
from datetime import date, datetime
from typing import Optional

from sqlalchemy import JSON, Boolean, Date, DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint
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
    # WhatsApp recipient for services/notifications — E.164-ish, see
    # core/phone.py. Optional: a staff user with no phone on file simply
    # never gets a WhatsApp send (email-only), same "never block the
    # caller" posture as a missing email.
    phone: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    hashed_password: Mapped[str] = mapped_column(String(128), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Bumped on password change to invalidate every JWT issued before that
    # point — see core/deps.py's get_current_user, ported from
    # PoultryOS-CBP's same convention.
    token_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Self-service "forgot password" (api/routers/auth.py) — hash-only
    # storage, same pattern as Deployment.registration_token_hash: this hub
    # only ever needs to *verify* the token a user presents back, never
    # present it again itself. NULL/expired = no reset in progress.
    password_reset_token_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    password_reset_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class Role(Base):
    """Role metadata for the admin Roles & Permissions UI (api/routers/rbac.py)
    — NOT the enforcement source of truth. Actual grants live in
    casbin_rule's p/g rows (services/rbac.py); this table only backs role
    CRUD (name/description) and `is_system`, which blocks renaming/deleting
    the two seeded roles ('admin', 'engineer') — their *permissions* can
    still be edited, just not their name, and they can't be deleted out
    from under every user holding them."""
    __tablename__ = "roles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class Permission(Base):
    """The reviewable permission catalog the Roles & Permissions UI's
    checkbox grid renders against (core/permissions.py's ALL_PERMISSIONS is
    what a migration seeds into this table). New permissions are added by a
    migration, never invented at runtime — see rbac.py's list_permissions."""
    __tablename__ = "permissions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    resource: Mapped[str] = mapped_column(String(100), nullable=False)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (UniqueConstraint("resource", "action", name="uq_permissions_resource_action"),)


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

    # The expiry_date value (from the latest DeploymentSnapshot) an
    # "expiring soon" alert has already been sent for — see
    # core/expiry_reminder_scheduler.py. NULL = never sent. Compared by
    # value, not just presence, so a renewal that pushes expiry_date
    # forward (reflected on the next heartbeat) naturally makes this stale
    # and lets a future reminder fire again for the new date, with no
    # explicit reset needed.
    expiry_reminder_sent_for: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    snapshots: Mapped[list["DeploymentSnapshot"]] = relationship(
        "DeploymentSnapshot", back_populates="deployment", order_by="DeploymentSnapshot.received_at.desc()",
    )


class DeploymentStaffAssignment(Base):
    """Many-to-many: which staff (User) are assigned to which Deployment.
    Narrows notification fan-out (services/notifications/recipients.py's
    recipients_for_deployment) to just the assigned staff for that
    deployment's tickets/subscription-request alerts, instead of every
    fleet-area permission holder — a deployment with no assignment still
    falls back to notifying everyone, so nothing breaks for an unassigned
    deployment.
    Purely a junction row — no own id, composite PK."""
    __tablename__ = "deployment_staff_assignments"

    deployment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("deployments.id"), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), primary_key=True)
    assigned_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


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


class OperationalEventStatus(str, enum.Enum):
    """The event's own outcome — deliberately generic (not one value per
    domain), so Phase 10's dashboard can bucket "everything that failed
    today" across deployments/tickets/maintenance/approvals with one WHERE
    clause instead of parsing event_type strings."""
    info = "info"          # a fact was recorded — no success/failure to report (e.g. ticket.created)
    pending = "pending"     # started, outcome not yet known (e.g. an approval was requested)
    success = "success"
    failure = "failure"


class OperationalEvent(Base):
    """Append-only cross-domain event log — HUB-Expansion.md Phase 1's "no
    operational action should disappear into a black box" spine. See
    docs/architecture/current-state.md for the full rationale; the short
    version:

    - Deliberately NOT a duplicate of WorkflowHistory (workflow/models.py),
      which stays the detailed per-instance/per-task trail for the approval
      engine. This table holds one coarser event per meaningful state
      transition across *every* domain, so a fleet-wide or per-deployment
      timeline (Phase 9) can query one table instead of hand-unioning every
      domain table.
    - entity_type/entity_id is a polymorphic reference with no FK — same
      shape as WorkflowInstance.business_object_type/_id (workflow/
      models.py) — because the referenced row can live in any table, or
      (a hard-deleted MaintenanceWindow) no longer exist at all.
    - event_type/source/actor_type/entity_type are plain strings, not
      native Postgres enums like every other status-shaped column in this
      codebase (see core/event_types.py's docstring for why: this catalog
      is expected to grow with nearly every future HUB-Expansion.md phase,
      and a native enum needs a migration per new value). `status` is a
      genuinely small, closed set, so it stays a native enum for
      consistency with the rest of the codebase.
    - causation_id is nullable and, as of Phase 1, never populated — the
      column is reserved for Phase 2's cross-system correlation chains
      (ticket -> PR -> release -> deployment) once something upstream of
      this hub (a GitHub/CI integration) exists to chain against.
    - customer_id is nullable and, as of Phase 1, never populated — there
      is no Customer table yet (see HUB-Expansion.md Phase 4); the column
      exists now so it doesn't need a follow-up migration once one lands.
    """
    __tablename__ = "operational_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(30), nullable=False)
    actor_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    entity_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    entity_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    deployment_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("deployments.id"), nullable=True, index=True)
    customer_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    correlation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    causation_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    status: Mapped[OperationalEventStatus] = mapped_column(Enum(OperationalEventStatus), default=OperationalEventStatus.info, nullable=False)
    event_metadata: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    deployment: Mapped[Optional["Deployment"]] = relationship("Deployment")


class NotificationChannel(str, enum.Enum):
    email = "email"
    whatsapp = "whatsapp"


class NotificationStatus(str, enum.Enum):
    pending = "pending"    # log row created, not yet handed to a channel
    sending = "sending"    # Celery task picked it up, provider call in flight
    sent = "sent"
    failed = "failed"
    cancelled = "cancelled"  # a kill switch (settings.NOTIFICATIONS_ENABLED /
                              # WHATSAPP_NOTIFICATIONS_ENABLED) was off — the
                              # attempt is still recorded, never silently dropped


class NotificationLog(Base):
    """One row per notification attempt — see services/notifications/. Never
    stores a secret in `payload` (there's no OTP/reset-token template here,
    but see constants.SENSITIVE_CONTEXT_KEYS for the same defense-in-depth
    PoultryPro-CBF uses, kept for any future template that needs it)."""
    __tablename__ = "notification_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    channel: Mapped[NotificationChannel] = mapped_column(Enum(NotificationChannel), nullable=False)
    provider: Mapped[str] = mapped_column(String(30), nullable=False)
    recipient: Mapped[str] = mapped_column(String(200), nullable=False)
    subject: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    template: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    # JSON-encoded sanitized template context — for display in the admin
    # notifications list and for resend (see services/notifications/repository.py).
    payload: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[NotificationStatus] = mapped_column(Enum(NotificationStatus), default=NotificationStatus.pending, nullable=False)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
