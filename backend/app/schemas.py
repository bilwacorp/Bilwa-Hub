import uuid
from datetime import date, datetime, timezone
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, field_serializer, field_validator

from app.core.url_safety import validate_url_format
from app.models import (
    DeploymentActionAttemptStatus, DeploymentActionExecutionStatus, DeploymentEnvironment, DeploymentReleaseSource,
    DeploymentStatus, MaintenanceWindowStatus, NotificationChannel, NotificationStatus, OperationalEventStatus,
    SupportTicketStatus, TicketLinkType,
)

# How hard an active maintenance window bites (MaintenanceWindow.mode):
#   banner    — in-app notice only
#   read_only — deployment 503s mutating requests
#   lockout   — read_only + new sign-ins refused
MaintenanceMode = Literal["banner", "read_only", "lockout"]


def _to_naive_utc(dt: datetime) -> datetime:
    """Every datetime column here is naive UTC. The frontend sends
    `.toISOString()` (a '...Z' aware value) — convert to UTC and drop
    tzinfo so it stores cleanly and compares against datetime.utcnow()."""
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _utc_iso(dt: Optional[datetime]) -> Optional[str]:
    """All datetime columns here are naive UTC. Pydantic would serialize
    them with no offset, and a browser's `new Date("2026-09-10T14:00:00")`
    then parses that as *local* time — an IST client's maintenance-window
    math would be 5.5h off. Emit an explicit +00:00 so every consumer
    (deployment gate, in-app banner, hub UI) parses UTC unambiguously."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


# ── auth ─────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    expires_in: int


# A role name — roles are dynamic/DB-backed now (see Role in app/models.py
# and api/routers/rbac.py), so this is deliberately just `str`, not a fixed
# Literal of the two seeded roles ('admin', 'engineer') the hub shipped
# with. Validity (does this role actually exist) is checked server-side
# against the `roles` table, not at the Pydantic type level.
StaffRole = str


class UserOut(BaseModel):
    id: uuid.UUID
    username: str
    full_name: Optional[str]
    email: Optional[str]
    role: Optional[StaffRole] = None
    # "resource.action" strings — what this logged-in user can actually do,
    # via whichever role they hold. The frontend gates UI elements with
    # can(permission) against this instead of a hardcoded role-name check,
    # which breaks down once custom roles exist (see services/rbac.py's
    # get_permissions_for_user).
    permissions: List[str] = []

    model_config = {"from_attributes": True}


# ── staff / user management ─────────────────────────────────────────────

class StaffUserCreate(BaseModel):
    username: str
    full_name: Optional[str] = None
    email: Optional[str] = None
    # E.164-ish WhatsApp recipient (see core/phone.py) — optional; a staff
    # user with no phone on file simply never gets a WhatsApp alert.
    phone: Optional[str] = None
    password: str
    role: StaffRole


class StaffUserUpdate(BaseModel):
    full_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    is_active: Optional[bool] = None
    role: Optional[StaffRole] = None


class StaffUserOut(BaseModel):
    id: uuid.UUID
    username: str
    full_name: Optional[str]
    email: Optional[str]
    phone: Optional[str] = None
    is_active: bool
    role: Optional[StaffRole] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class StaffUserListResponse(BaseModel):
    total: int
    items: List[StaffUserOut]


class PasswordResetRequest(BaseModel):
    new_password: str


# ── self-service forgot password (api/routers/auth.py) ─────────────────
# Deliberately separate from PasswordResetRequest above, which is the
# admin-resets-another-user shape (POST /users/{id}/reset-password) — this
# pair is public/unauthenticated.

class ForgotPasswordRequest(BaseModel):
    username: str


class ResetPasswordConfirmRequest(BaseModel):
    token: str
    new_password: str


# ── registration / ingest ───────────────────────────────────────────────

class RegisterRequest(BaseModel):
    registration_token: str
    base_url: str
    app_version: Optional[str] = None
    action_key: str

    @field_validator("base_url")
    @classmethod
    def _safe_base_url(cls, v: str) -> str:
        # HUB-Expansion.md Phase 15 — the cheap, synchronous half of the
        # SSRF check; see app/core/url_safety.py's module docstring for
        # the full two-layer design (this schema validator only catches
        # scheme/format/literal-IP cases — api/routers/register.py does
        # the async DNS-resolution check this can't do here).
        return validate_url_format(v)


class RegisterResponse(BaseModel):
    deployment_id: uuid.UUID
    api_key: str


class SubscriptionSnapshotIn(BaseModel):
    plan_name: Optional[str] = None
    status: Optional[str] = None
    expiry_date: Optional[date] = None
    trial_ends_at: Optional[date] = None
    auto_renew: Optional[bool] = None


class UsageItemIn(BaseModel):
    key: str
    label: str
    current: int
    limit: Optional[int] = None


class PendingRequestIn(BaseModel):
    id: str
    request_type: str
    requested_plan_name: Optional[str] = None
    message: Optional[str] = None
    requested_at: datetime


class HeartbeatRequest(BaseModel):
    app_version: Optional[str] = None
    subscription: Optional[SubscriptionSnapshotIn] = None
    usage: List[UsageItemIn] = []
    pending_requests: List[PendingRequestIn] = []


class MaintenanceWindowPublic(BaseModel):
    """The window shape sent to a deployment — in the heartbeat response and
    in the hub->deployment push. Timestamps carry an explicit UTC offset
    (see _utc_iso)."""
    id: uuid.UUID
    scheduled_start: datetime
    scheduled_end: datetime
    description: str
    status: MaintenanceWindowStatus
    mode: MaintenanceMode

    model_config = {"from_attributes": True}

    @field_serializer("scheduled_start", "scheduled_end")
    def _ser_dt(self, dt: datetime) -> str:
        return _utc_iso(dt)


class HeartbeatResponse(BaseModel):
    status: str = "ok"
    maintenance: List[MaintenanceWindowPublic] = []


class MaintenanceNotify(BaseModel):
    window_id: uuid.UUID
    kind: Literal["scheduled", "reminder"]


class MaintenancePushRequest(BaseModel):
    """Body of POST {deployment}/api/v1/hub/maintenance. `maintenance` is the
    deployment's full current window list (a replace, not a merge). `notify`,
    when set, names one window + kind the deployment should email its admins
    about."""
    maintenance: List[MaintenanceWindowPublic]
    notify: Optional[MaintenanceNotify] = None


class SupportTicketIngest(BaseModel):
    subject: str
    description: str
    priority: str = "normal"
    submitted_by_name: Optional[str] = None
    submitted_by_email: Optional[str] = None
    submitted_at: Optional[datetime] = None


# ── deployments (staff) ─────────────────────────────────────────────────

# ── customers / applications (HUB-Expansion.md Phase 4) ───────────────────

class CustomerOut(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    notes: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}


class CustomerCreate(BaseModel):
    name: str
    slug: str
    notes: Optional[str] = None


class CustomerUpdate(BaseModel):
    name: Optional[str] = None
    notes: Optional[str] = None


class ApplicationOut(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    description: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}


class ApplicationCreate(BaseModel):
    name: str
    slug: str
    description: Optional[str] = None


class ApplicationUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None


# ── deployment lineage (HUB-Expansion.md Phase 4) ─────────────────────────

class DeploymentLineageUpdate(BaseModel):
    """PATCH body for /deployments/{id}/lineage — every field optional so
    a caller can update just one of the three without re-sending the
    others. An explicit null clears customer_id/application_id (unsets
    the link); environment, being non-nullable on the model, simply isn't
    included in the request if left unchanged."""
    customer_id: Optional[uuid.UUID] = None
    application_id: Optional[uuid.UUID] = None
    environment: Optional[DeploymentEnvironment] = None


class DeploymentReleaseCreate(BaseModel):
    """Manual entry (source is always forced to 'manual' by the router,
    never taken from the request body — see api/routers/deployments.py)."""
    version: Optional[str] = None
    repository_id: Optional[uuid.UUID] = None
    release_id: Optional[uuid.UUID] = None
    commit_sha: Optional[str] = None
    deployed_by: Optional[str] = None
    deployed_at: Optional[datetime] = None
    notes: Optional[str] = None

    @field_validator("deployed_at")
    @classmethod
    def _naive_utc(cls, v):
        return _to_naive_utc(v) if v else v


class DeploymentReleaseOut(BaseModel):
    id: uuid.UUID
    deployment_id: uuid.UUID
    version: Optional[str]
    repository_id: Optional[uuid.UUID]
    release_id: Optional[uuid.UUID]
    commit_sha: Optional[str]
    source: DeploymentReleaseSource
    deployed_by: Optional[str]
    deployed_at: datetime
    notes: Optional[str]
    created_at: datetime
    # Denormalized display fields, filled in by the router from a join —
    # never populated by model_validate(release) alone (see
    # api/routers/deployments.py's _release_out).
    repository_full_name: Optional[str] = None
    release_tag_name: Optional[str] = None

    model_config = {"from_attributes": True}


class DeploymentCreate(BaseModel):
    client_name: str
    slug: str


class DeploymentCreateOut(BaseModel):
    id: uuid.UUID
    client_name: str
    slug: str
    status: DeploymentStatus
    registration_token: str  # plaintext — shown exactly once, on creation


class DeploymentSnapshotOut(BaseModel):
    plan_name: Optional[str]
    subscription_status: Optional[str]
    expiry_date: Optional[date]
    trial_ends_at: Optional[date]
    auto_renew: Optional[bool]
    usage: Optional[list]
    pending_requests: Optional[list]
    app_version: Optional[str]
    received_at: datetime

    model_config = {"from_attributes": True}


class StaffOptionOut(BaseModel):
    """Minimal staff shape for a picker — deliberately not the full
    StaffUserOut (role/is_active/created_at), since this is exposed to
    anyone with DEPLOYMENTS_ASSIGN_STAFF, not the STAFF_VIEW/STAFF_* set
    (see api/routers/users.py)."""
    id: uuid.UUID
    username: str
    full_name: Optional[str]

    model_config = {"from_attributes": True}


class DeploymentStaffAssignRequest(BaseModel):
    """Replaces the full assignment set for a deployment — not an
    incremental add/remove, same 'replace wholesale' shape as
    services/rbac.set_role."""
    user_ids: List[uuid.UUID]


class DeploymentOut(BaseModel):
    id: uuid.UUID
    client_name: str
    slug: str
    base_url: Optional[str]
    status: DeploymentStatus
    created_at: datetime
    latest_snapshot: Optional[DeploymentSnapshotOut] = None
    # Seconds since the last heartbeat (None if never). derived_status folds
    # that together with any active maintenance window into one label the
    # deployments list renders directly — see api/routers/deployments.py.
    heartbeat_age_seconds: Optional[int] = None
    derived_status: Literal["never", "online", "stale", "offline", "maintenance"] = "never"
    # Staff assigned to this deployment (services/notifications/recipients.py
    # narrows ticket/subscription-request alerts to just these when non-empty).
    assigned_staff: List[StaffOptionOut] = []
    # HUB-Expansion.md Phase 4 lineage — customer/application are None when
    # unlinked (both optional, see app/models.py's Customer/Application).
    # current_release is the most recent DeploymentRelease row, if any.
    environment: DeploymentEnvironment = DeploymentEnvironment.production
    customer_id: Optional[uuid.UUID] = None
    application_id: Optional[uuid.UUID] = None
    customer: Optional[CustomerOut] = None
    application: Optional[ApplicationOut] = None
    current_release: Optional[DeploymentReleaseOut] = None

    model_config = {"from_attributes": True}


class DeploymentListResponse(BaseModel):
    total: int
    items: List[DeploymentOut]


class ReissueTokenOut(BaseModel):
    registration_token: str


class RenewActionRequest(BaseModel):
    new_expiry_date: date
    renewal_amount: Optional[float] = None


class SuspendActionRequest(BaseModel):
    reason: str


class ChangePlanActionRequest(BaseModel):
    new_plan_id: uuid.UUID


class ExtendExpiryActionRequest(BaseModel):
    new_expiry_date: date


class SubscriptionRequestReviewAction(BaseModel):
    status: str
    review_note: Optional[str] = None


# ── tickets ──────────────────────────────────────────────────────────────

class SupportTicketOut(BaseModel):
    id: uuid.UUID
    deployment_id: uuid.UUID
    subject: str
    description: str
    priority: str
    status: SupportTicketStatus
    submitted_by_name: Optional[str]
    submitted_by_email: Optional[str]
    created_at: datetime
    resolved_at: Optional[datetime]

    model_config = {"from_attributes": True}


class SupportTicketListResponse(BaseModel):
    total: int
    items: List[SupportTicketOut]


class SupportTicketUpdate(BaseModel):
    status: SupportTicketStatus


# ── ticket links (HUB-Expansion.md Phase 6) ───────────────────────────────

class SupportTicketLinkCreate(BaseModel):
    link_type: TicketLinkType
    target_id: uuid.UUID


class SupportTicketLinkOut(BaseModel):
    id: uuid.UUID
    ticket_id: uuid.UUID
    link_type: TicketLinkType
    target_id: uuid.UUID
    created_by: Optional[uuid.UUID]
    created_at: datetime
    # Resolved server-side by services/ticket_links.resolve_link_display —
    # never populated by model_validate(link) alone, same denormalization
    # shape as DeploymentReleaseOut.repository_full_name (Phase 4).
    label: str = ""
    url: Optional[str] = None
    target_status: Optional[str] = None

    model_config = {"from_attributes": True}


# ── maintenance windows ─────────────────────────────────────────────────

class MaintenanceWindowCreate(BaseModel):
    deployment_id: Optional[uuid.UUID] = None
    scheduled_start: datetime
    scheduled_end: datetime
    description: str
    mode: MaintenanceMode = "banner"
    expected_impact: Optional[str] = None
    # HUB-Expansion.md Phase 7 — when true, the window is created as
    # `draft` (never pushed, never routed for approval) instead of going
    # through the create-time gate-or-schedule decision (api/routers/
    # maintenance.py's create_window -> app/approvals/maintenance_hooks
    # .route_window). Default false preserves the pre-Phase-7 behavior of
    # a plain create going straight to `planned` for the common case.
    save_as_draft: bool = False

    @field_validator("scheduled_start", "scheduled_end")
    @classmethod
    def _naive_utc(cls, v: datetime) -> datetime:
        return _to_naive_utc(v)


class MaintenanceWindowUpdate(BaseModel):
    scheduled_start: Optional[datetime] = None
    scheduled_end: Optional[datetime] = None
    description: Optional[str] = None
    status: Optional[MaintenanceWindowStatus] = None
    mode: Optional[MaintenanceMode] = None
    expected_impact: Optional[str] = None
    actual_impact: Optional[str] = None

    @field_validator("scheduled_start", "scheduled_end")
    @classmethod
    def _naive_utc(cls, v: Optional[datetime]) -> Optional[datetime]:
        return _to_naive_utc(v) if v is not None else v


class MaintenanceWindowOut(BaseModel):
    id: uuid.UUID
    deployment_id: Optional[uuid.UUID]
    scheduled_start: datetime
    scheduled_end: datetime
    description: str
    status: MaintenanceWindowStatus
    mode: MaintenanceMode
    expected_impact: Optional[str]
    actual_impact: Optional[str]
    approved_by: Optional[uuid.UUID]
    created_by: Optional[uuid.UUID]
    created_at: datetime

    model_config = {"from_attributes": True}

    @field_serializer("scheduled_start", "scheduled_end")
    def _ser_dt(self, dt: datetime) -> str:
        return _utc_iso(dt)


class MaintenanceWindowListResponse(BaseModel):
    total: int
    items: List[MaintenanceWindowOut]


# ── notifications ────────────────────────────────────────────────────────

class NotificationLogOut(BaseModel):
    id: uuid.UUID
    channel: NotificationChannel
    provider: str
    recipient: str
    subject: Optional[str]
    template: Optional[str]
    status: NotificationStatus
    error_message: Optional[str]
    retry_count: int
    sent_at: Optional[datetime]
    created_at: datetime

    model_config = {"from_attributes": True}


class NotificationLogListResponse(BaseModel):
    total: int
    items: List[NotificationLogOut]


class NotificationSendResponse(BaseModel):
    notification_id: uuid.UUID
    status: NotificationStatus


class TestEmailRequest(BaseModel):
    recipient: str


class TestWhatsAppRequest(BaseModel):
    recipient: str
    message: str = "This is a test message from BilwaCorp Fleet Hub."


# ── RBAC: permission catalog + custom roles (api/routers/rbac.py) ─────────

class PermissionOut(BaseModel):
    id: uuid.UUID
    resource: str
    action: str
    description: Optional[str]

    model_config = {"from_attributes": True}


class RoleOut(BaseModel):
    id: uuid.UUID
    name: str
    description: Optional[str]
    is_system: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class RoleCreate(BaseModel):
    # Lowercase/digits/underscore only — this is also the literal Casbin
    # role name (casbin_rule.v0/v1), not just a display label.
    name: str = Field(pattern=r"^[a-z0-9_]+$", min_length=1, max_length=50)
    description: Optional[str] = None


class RoleUpdate(BaseModel):
    name: Optional[str] = Field(default=None, pattern=r"^[a-z0-9_]+$", min_length=1, max_length=50)
    description: Optional[str] = None


class RolePermissionsUpdate(BaseModel):
    # "resource.action" strings — replaces the role's full permission set.
    permissions: List[str]


# ── operational events (HUB-Expansion.md Phase 1) ──────────────────────────

class OperationalEventOut(BaseModel):
    id: uuid.UUID
    event_type: str
    source: str
    actor_type: str
    actor_id: Optional[uuid.UUID]
    entity_type: Optional[str]
    entity_id: Optional[uuid.UUID]
    deployment_id: Optional[uuid.UUID]
    customer_id: Optional[uuid.UUID]
    correlation_id: uuid.UUID
    causation_id: Optional[uuid.UUID]
    status: OperationalEventStatus
    event_metadata: Optional[dict] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class OperationalEventListResponse(BaseModel):
    total: int
    items: List[OperationalEventOut]


# ── deployment action executions (HUB-Expansion.md Phase 12/13) ───────────

class DeploymentActionAttemptOut(BaseModel):
    id: uuid.UUID
    attempt_number: int
    status: DeploymentActionAttemptStatus
    error: Optional[str]
    response: Optional[dict]
    triggered_by: Optional[uuid.UUID]
    started_at: datetime
    finished_at: datetime

    model_config = {"from_attributes": True}


class DeploymentActionExecutionOut(BaseModel):
    id: uuid.UUID
    workflow_instance_id: uuid.UUID
    deployment_id: uuid.UUID
    action_key: str
    idempotency_key: str
    correlation_id: Optional[uuid.UUID]
    status: DeploymentActionExecutionStatus
    attempt_count: int
    last_attempted_at: Optional[datetime]
    last_error: Optional[str]
    last_response: Optional[dict]
    created_at: datetime
    updated_at: datetime
    attempts: List[DeploymentActionAttemptOut] = []

    model_config = {"from_attributes": True}


# ── operations dashboard (HUB-Expansion.md Phase 10) ──────────────────────
# See app/services/dashboard.py for how every field here is computed —
# each one is a real query over existing data, not a placeholder; the
# module docstring there also documents which sub-sections quietly
# zero out (rather than 403) when the caller lacks that domain's own
# view permission.

class DashboardFleetSummary(BaseModel):
    total: int
    healthy: int
    warning: int
    offline: int
    unknown: int
    under_maintenance: int


class DashboardReleaseItem(BaseModel):
    deployment_id: uuid.UUID
    client_name: str
    version: Optional[str]
    deployed_at: datetime


class DashboardFailedActionItem(BaseModel):
    deployment_id: uuid.UUID
    client_name: str
    action_key: str
    last_error: Optional[str]
    last_attempted_at: Optional[datetime]


class DashboardDeploymentsSummary(BaseModel):
    recently_deployed: List[DashboardReleaseItem]
    recently_failed: List[DashboardFailedActionItem]
    outdated_versions: int
    missing_heartbeat: int
    high_risk: int


class DashboardSupportSummary(BaseModel):
    open: int
    unassigned: int
    escalated: int
    awaiting_engineering: int


class DashboardMaintenanceSummary(BaseModel):
    upcoming: int
    active: int
    failed: int


class DashboardApprovalsSummary(BaseModel):
    pending: int
    overdue: int
    recently_approved: int
    recently_rejected: int


class DashboardIntegrationsSummary(BaseModel):
    github_webhook_failures: int
    deployment_callback_failures: int
    notification_failures: int


class DashboardAttentionItem(BaseModel):
    kind: str
    severity: Literal["warning", "critical"]
    message: str
    deployment_id: Optional[uuid.UUID] = None


class DashboardOut(BaseModel):
    fleet: DashboardFleetSummary
    deployments: DashboardDeploymentsSummary
    support: DashboardSupportSummary
    maintenance: DashboardMaintenanceSummary
    approvals: DashboardApprovalsSummary
    integrations: DashboardIntegrationsSummary
    attention: List[DashboardAttentionItem]


# ── integration center (HUB-Expansion.md Phase 11) ────────────────────────

class IntegrationSummaryOut(BaseModel):
    key: str
    name: str
    status: Literal["connected", "disconnected", "error", "not_configured"]
    connected: bool
    last_sync_at: Optional[datetime] = None
    last_webhook_at: Optional[datetime] = None
    last_error: Optional[str] = None
    last_error_at: Optional[datetime] = None
    detail: Optional[str] = None
    # Set only for a GitHub card — the frontend's "Test Connection" button
    # needs this to call POST /github/integrations/{id}/test-connection.
    integration_id: Optional[uuid.UUID] = None
