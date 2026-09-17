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


class DeploymentEnvironment(str, enum.Enum):
    """HUB-Expansion.md Phase 4's "Environment" lineage field. A HUB-owned
    closed set (ADR-001's enum-vs-string rule), unlike e.g.
    GitHubPullRequest.state which mirrors an external vocabulary. Almost
    every row today is 'production' (CLAUDE.md: each client deployment is
    its own single-tenant, separately hosted instance) — staging/development/
    uat exist for the rarer non-client instance (e.g. a BilwaCorp-internal
    demo or pre-prod box) that still registers with this same hub."""
    production = "production"
    staging = "staging"
    development = "development"
    uat = "uat"


class Customer(Base):
    """HUB-Expansion.md Phase 4. The organization a Deployment belongs to —
    deliberately additive, not a replacement for Deployment.client_name
    (which every existing query/notification/UI already reads and keeps
    reading unchanged, per the plan's "DO NOT rewrite the existing
    application" rule). Deployment.customer_id is nullable and optional:
    a deployment with no customer link still works exactly as before,
    just without the grouping. One customer can own more than one
    deployment (e.g. separate prod/staging instances)."""
    __tablename__ = "customers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class Application(Base):
    """HUB-Expansion.md Phase 4. The software product a Deployment is
    running (e.g. "PoultryOS-CBP") — a grouping layer above the Phase 3
    Deployment<->GitHubRepository M:N edge (ADR-003/target-state.md: "build
    Application on top of it", not a migration of that relationship).
    Deployment.application_id is nullable/optional for the same reason as
    Customer above."""
    __tablename__ = "applications"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


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

    # HUB-Expansion.md Phase 4 lineage fields — both nullable/optional, see
    # Customer/Application's own docstrings for why.
    customer_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("customers.id"), nullable=True)
    application_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("applications.id"), nullable=True)
    environment: Mapped[DeploymentEnvironment] = mapped_column(
        Enum(DeploymentEnvironment), default=DeploymentEnvironment.production, nullable=False,
    )

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
    # Deliberately no customer/application relationship() here — this
    # codebase never triggers a lazy load implicitly under AsyncSession
    # (see DeploymentActionExecutionOut's _execution_out docstring for the
    # same rule applied to .attempts); callers batch-query Customer/
    # Application explicitly instead, same shape as _assigned_staff_map.


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


class DeploymentReleaseSource(str, enum.Enum):
    """HUB-Expansion.md Phase 4's "Deployment source" lineage field — a
    HUB-owned closed set. 'heartbeat_inferred' is the only source this
    phase populates automatically (services/lineage.py's
    infer_release_from_heartbeat, called from ingest.py on every
    heartbeat whose app_version changed) by matching the reported version
    against a GitHubRelease tag on one of the deployment's linked repos
    (Phase 3's DeploymentGitHubRepository). 'manual' is a staff-entered
    correction/backfill (POST /deployments/{id}/releases). 'github_actions'
    /'ci_cd' are reserved for Phase 5's CI/CD webhook integration — this
    phase only defines the shape, it does not implement that source."""
    manual = "manual"
    heartbeat_inferred = "heartbeat_inferred"
    github_actions = "github_actions"
    ci_cd = "ci_cd"


class DeploymentRelease(Base):
    """HUB-Expansion.md Phase 4. One row per recorded "this deployment
    started running this version" event — historized like
    DeploymentSnapshot rather than a single mutable "current version"
    column, so the lineage view can show a real history, not just a
    snapshot-in-time. The most recent row (by deployed_at) for a
    deployment is its "current" release. repository_id/release_id are
    plain UUID FK columns (not ORM relationships) into
    app.integrations.github.models's GitHubRepository/GitHubRelease —
    deliberately, so this core module never imports that integration
    package; callers resolve the join explicitly the same way
    api/routers/deployments.py's GitHub panel already does."""
    __tablename__ = "deployment_releases"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    deployment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("deployments.id", ondelete="CASCADE"), nullable=False)
    version: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    repository_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("github_repositories.id"), nullable=True)
    release_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("github_releases.id"), nullable=True)
    commit_sha: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    source: Mapped[DeploymentReleaseSource] = mapped_column(Enum(DeploymentReleaseSource), nullable=False)
    deployed_by: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    deployed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class DeploymentActionExecutionStatus(str, enum.Enum):
    """HUB-Expansion.md Phase 12's four states, distinguishing "the approval
    succeeded" (WorkflowInstance.status == completed, see workflow/models.py)
    from "the deferred call to the deployment actually succeeded" — the gap
    where the app previously showed nothing but an application log line.
    Values map 1:1 to the doc's own naming: pending=EXECUTION_PENDING,
    executing=EXECUTING, executed=EXECUTED, failed=EXECUTION_FAILED."""
    pending = "pending"
    executing = "executing"
    executed = "executed"
    failed = "failed"


class DeploymentActionAttemptStatus(str, enum.Enum):
    success = "success"
    failure = "failure"


class DeploymentActionExecution(Base):
    """One row per gated deployment action (renew/suspend/change_plan) that
    has an approval instance — created the moment the approval is requested
    (app/approvals/deployment_hooks.py's request_or_execute), independently
    of whether/when it's ever approved, so an operator can find "what's
    waiting to execute" before anyone has acted on it. idempotency_key
    (`hub-action-{workflow_instance_id}`, per HUB-Expansion.md Phase 13) is
    sent as X-Idempotency-Key on every attempt including retries — see
    services/deployment_client.py — but the real backstop against a
    duplicate execution is local: `status` only ever leaves `executed`
    through a fresh row, never through a retry (see deployment_hooks.py's
    _execute, which no-ops a retry against an already-`executed` row)."""
    __tablename__ = "deployment_action_executions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workflow_instance_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("workflow_instances.id", ondelete="CASCADE"), unique=True, nullable=False)
    deployment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("deployments.id"), nullable=False)
    action_key: Mapped[str] = mapped_column(String(50), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    # Copied from the WorkflowInstance at creation time so every
    # OperationalEvent this row's attempts emit can share it without an
    # extra join back to workflow_instances on every retry.
    correlation_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    status: Mapped[DeploymentActionExecutionStatus] = mapped_column(
        Enum(DeploymentActionExecutionStatus), default=DeploymentActionExecutionStatus.pending, nullable=False,
    )
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_attempted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_response: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    deployment: Mapped["Deployment"] = relationship("Deployment")
    attempts: Mapped[list["DeploymentActionAttempt"]] = relationship(
        "DeploymentActionAttempt", back_populates="execution", cascade="all, delete-orphan",
        order_by="DeploymentActionAttempt.attempt_number",
    )


class DeploymentActionAttempt(Base):
    """One row per attempt at executing a DeploymentActionExecution — the
    "retry history" HUB-Expansion.md Phase 12 asks for. triggered_by is
    NULL for the automatic first attempt (fired by the completion hook the
    moment the approval completes) and set to the acting staff member's id
    for every manual retry (POST .../action-executions/{id}/retry)."""
    __tablename__ = "deployment_action_attempts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    execution_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("deployment_action_executions.id", ondelete="CASCADE"), nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[DeploymentActionAttemptStatus] = mapped_column(Enum(DeploymentActionAttemptStatus), nullable=False)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    response: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    triggered_by: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    finished_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    execution: Mapped["DeploymentActionExecution"] = relationship("DeploymentActionExecution", back_populates="attempts")


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


class TicketLinkType(str, enum.Enum):
    """HUB-Expansion.md Phase 6 — a HUB-owned closed set of what a
    SupportTicket can link to. Deliberately no 'incident' member — no
    Incident concept exists anywhere in this codebase (see
    docs/adr/ADR-006-support-engineering-link.md decision 1); a
    deployment link isn't here either, since SupportTicket.deployment_id
    already covers that (Phase 1), and duplicating it as a link_type
    would just be two ways to express the same fact."""
    github_issue = "github_issue"
    github_pull_request = "github_pull_request"
    github_release = "github_release"
    maintenance_window = "maintenance_window"


class SupportTicketLink(Base):
    """Links a SupportTicket to a GitHub issue/PR/release or a
    MaintenanceWindow. target_id is a plain UUID with no FK constraint —
    same polymorphic-reference shape as OperationalEvent.entity_id, since
    which table it points into depends on link_type. services/
    ticket_links.py resolves the join and validates target existence at
    creation time; there's no ORM relationship() to any of those tables
    for the same "core app/models.py never imports the GitHub integration
    package" reason as DeploymentRelease (see ADR-004 decision 4)."""
    __tablename__ = "support_ticket_links"
    __table_args__ = (UniqueConstraint("ticket_id", "link_type", "target_id", name="uq_ticket_link_target"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ticket_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("support_tickets.id", ondelete="CASCADE"), nullable=False)
    link_type: Mapped[TicketLinkType] = mapped_column(Enum(TicketLinkType), nullable=False)
    target_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class MaintenanceWindowStatus(str, enum.Enum):
    """HUB-Expansion.md Phase 7 widened this from four values to nine.
    `planned`/`in_progress` are NOT renamed to the phase's own
    "scheduled"/"active" vocabulary — see
    docs/adr/ADR-007-maintenance-lifecycle-and-approvals.md decision 1 for
    why keeping the original member names (while treating them as
    synonyms in every user-facing label) is the correct reading of "existing
    behavior must remain compatible" versus a literal rename.

    draft              — being authored; never pushed to any deployment,
                          never touched by the scheduler.
    approval_required  — submitted, but this window is "high risk" (see
                          app/approvals/maintenance_hooks.py's
                          _is_high_risk) and an active/published
                          maintenance_window_approval workflow exists.
    approved           — approval completed; the very next scheduler tick
                          promotes this straight to `planned` (see
                          core/maintenance_scheduler.py's _auto_transition)
                          — there's no additional condition to wait on.
    planned            — "scheduled": will auto-activate at scheduled_start.
    notification       — planned, AND the advance-notice push to its
                          target(s) has gone out (see services/
                          maintenance_push.py's sync_window). Purely
                          informational — treated identically to `planned`
                          everywhere else (push-pending queries, the
                          to_start/expired scheduler queries).
    in_progress        — "active": currently within its scheduled window.
    completed          — ended normally (by clock, or manually via PATCH).
    failed             — ended badly; a MANUAL terminal status only (see
                          the ADR's decision on why this codebase has no
                          live signal to auto-detect a maintenance failure
                          from, unlike DeploymentActionExecution's real
                          HTTP call outcome).
    cancelled          — pre-existing; unchanged.
    """
    draft = "draft"
    approval_required = "approval_required"
    approved = "approved"
    planned = "planned"
    notification = "notification"
    in_progress = "in_progress"
    completed = "completed"
    failed = "failed"
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
    # HUB-Expansion.md Phase 7 additions.
    expected_impact: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    actual_impact: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    approved_by: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

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
