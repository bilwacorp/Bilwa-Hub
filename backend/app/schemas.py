import uuid
from datetime import date, datetime
from typing import List, Optional

from pydantic import BaseModel

from app.models import DeploymentStatus, MaintenanceWindowStatus, SupportTicketStatus


# ── auth ─────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    expires_in: int


class UserOut(BaseModel):
    id: uuid.UUID
    username: str
    full_name: Optional[str]
    email: Optional[str]

    model_config = {"from_attributes": True}


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


class DeploymentOut(BaseModel):
    id: uuid.UUID
    client_name: str
    slug: str
    base_url: Optional[str]
    status: DeploymentStatus
    created_at: datetime
    latest_snapshot: Optional[DeploymentSnapshotOut] = None

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


class MaintenanceWindowUpdate(BaseModel):
    scheduled_start: Optional[datetime] = None
    scheduled_end: Optional[datetime] = None
    description: Optional[str] = None
    status: Optional[MaintenanceWindowStatus] = None


class MaintenanceWindowOut(BaseModel):
    id: uuid.UUID
    deployment_id: Optional[uuid.UUID]
    scheduled_start: datetime
    scheduled_end: datetime
    description: str
    status: MaintenanceWindowStatus
    created_at: datetime

    model_config = {"from_attributes": True}


class MaintenanceWindowListResponse(BaseModel):
    total: int
    items: List[MaintenanceWindowOut]
