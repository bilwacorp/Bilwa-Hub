import uuid
from datetime import date, datetime, timezone
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, field_serializer, field_validator

from app.models import (
    DeploymentStatus, MaintenanceWindowStatus, NotificationChannel, NotificationStatus, SupportTicketStatus,
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
    anyone with DEPLOYMENTS_MANAGE, not just STAFF_MANAGE (admin-only, see
    api/routers/users.py)."""
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


# ── maintenance windows ─────────────────────────────────────────────────

class MaintenanceWindowCreate(BaseModel):
    deployment_id: Optional[uuid.UUID] = None
    scheduled_start: datetime
    scheduled_end: datetime
    description: str
    mode: MaintenanceMode = "banner"

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
