"""Staff-facing deployment registry + inbound-action triggers. One
permission per action (see core/permissions.py) rather than one router-
level gate — a role can hold e.g. deployments.renew without
deployments.suspend. GET/list/detail and every route taking a
deployment_id are additionally row-scoped: a caller without
DEPLOYMENTS_VIEW_ALL only sees/touches deployments explicitly assigned to
them (_get_visible_or_404) — see core/permissions.py's "Row-level
visibility" section."""
import hashlib
import secrets
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import event_types as et
from app.core.config import settings
from app.core.permissions import (
    ACTIONS_RETRY, DEPLOYMENTS_ASSIGN_STAFF, DEPLOYMENTS_CHANGE_PLAN, DEPLOYMENTS_CHECK_HEALTH, DEPLOYMENTS_CREATE,
    DEPLOYMENTS_EXTEND_EXPIRY, DEPLOYMENTS_RENEW, DEPLOYMENTS_REVIEW_REQUEST, DEPLOYMENTS_SUSPEND,
    DEPLOYMENTS_VIEW, DEPLOYMENTS_VIEW_ALL, has_permission, require_permission,
)
from app.db.session import get_db
from app.models import (
    Deployment, DeploymentActionAttempt, DeploymentActionExecution, DeploymentSnapshot, DeploymentStaffAssignment,
    DeploymentStatus, MaintenanceWindow, OperationalEventStatus, User,
)
from app.schemas import (
    ChangePlanActionRequest, DeploymentActionAttemptOut, DeploymentActionExecutionOut, DeploymentCreate,
    DeploymentCreateOut, DeploymentListResponse, DeploymentOut, DeploymentSnapshotOut, DeploymentStaffAssignRequest,
    ExtendExpiryActionRequest, ReissueTokenOut, RenewActionRequest, StaffOptionOut, SubscriptionRequestReviewAction,
    SuspendActionRequest,
)
from app.approvals import deployment_hooks
from app.services import deployment_client
from app.services.deployment_scope import assigned_deployment_ids
from app.services.events import record_event
from app.services.maintenance_query import currently_active_windows

router = APIRouter(prefix="/deployments", tags=["deployments"])


async def _latest_snapshot(db: AsyncSession, deployment_id) -> DeploymentSnapshot | None:
    return (await db.execute(
        select(DeploymentSnapshot).where(DeploymentSnapshot.deployment_id == deployment_id)
        .order_by(DeploymentSnapshot.received_at.desc()).limit(1)
    )).scalar_one_or_none()


async def _assigned_staff_map(db: AsyncSession, deployment_ids: list) -> dict:
    """One query for however many deployments are being rendered (list or
    detail) — avoids an assignment lookup per row."""
    if not deployment_ids:
        return {}
    rows = (await db.execute(
        select(DeploymentStaffAssignment.deployment_id, User)
        .join(User, User.id == DeploymentStaffAssignment.user_id)
        .where(DeploymentStaffAssignment.deployment_id.in_(deployment_ids))
        .order_by(User.username)
    )).all()
    out: dict = {}
    for deployment_id, user in rows:
        out.setdefault(deployment_id, []).append(StaffOptionOut.model_validate(user))
    return out


def _derive_status(d, snap, active_windows: list[MaintenanceWindow]):
    """(heartbeat_age_seconds, derived_status). A live maintenance window
    (this deployment or fleet-wide) shows as 'maintenance' regardless of
    heartbeat age — quiet-during-a-window is expected, not a fault."""
    in_maintenance = any(
        w.deployment_id is None or w.deployment_id == d.id for w in active_windows
    )
    if snap is None:
        return None, ("maintenance" if in_maintenance else "never")
    age = int((datetime.utcnow() - snap.received_at).total_seconds())
    if in_maintenance:
        return age, "maintenance"
    if age > settings.HEARTBEAT_OFFLINE_HOURS * 3600:
        return age, "offline"
    if age > settings.HEARTBEAT_STALE_HOURS * 3600:
        return age, "stale"
    return age, "online"


async def _to_out(
    db: AsyncSession, d: Deployment, active_windows: list[MaintenanceWindow], assigned_staff_map: dict | None = None,
) -> DeploymentOut:
    snap = await _latest_snapshot(db, d.id)
    out = DeploymentOut.model_validate(d)
    out.latest_snapshot = DeploymentSnapshotOut.model_validate(snap) if snap else None
    out.heartbeat_age_seconds, out.derived_status = _derive_status(d, snap, active_windows)
    if assigned_staff_map is not None:
        out.assigned_staff = assigned_staff_map.get(d.id, [])
    return out


async def _execution_out(db: AsyncSession, execution: DeploymentActionExecution) -> DeploymentActionExecutionOut:
    """Builds the response from explicit column values rather than
    `DeploymentActionExecutionOut.model_validate(execution)` — that would
    make Pydantic read `execution.attempts` too (it's a declared schema
    field), which is a lazy-loaded relationship this codebase never
    triggers implicitly (see workflow/repositories.py's list_*_for_instance
    functions, which always query explicitly instead)."""
    attempts = (await db.execute(
        select(DeploymentActionAttempt).where(DeploymentActionAttempt.execution_id == execution.id)
        .order_by(DeploymentActionAttempt.attempt_number)
    )).scalars().all()
    return DeploymentActionExecutionOut(
        id=execution.id, workflow_instance_id=execution.workflow_instance_id, deployment_id=execution.deployment_id,
        action_key=execution.action_key, idempotency_key=execution.idempotency_key,
        correlation_id=execution.correlation_id, status=execution.status, attempt_count=execution.attempt_count,
        last_attempted_at=execution.last_attempted_at, last_error=execution.last_error,
        last_response=execution.last_response, created_at=execution.created_at, updated_at=execution.updated_at,
        attempts=[DeploymentActionAttemptOut.model_validate(a) for a in attempts],
    )


async def _get_visible_or_404(db: AsyncSession, deployment_id: str, current_user: User) -> Deployment:
    """Like a plain get-or-404, except a deployment outside the caller's
    scope (not assigned to them, and they lack DEPLOYMENTS_VIEW_ALL) 404s
    exactly like a nonexistent one — enforced here so every route taking a
    deployment_id gets it "for free" via this one call, not just the list/
    detail GETs."""
    d = (await db.execute(select(Deployment).where(Deployment.id == deployment_id))).scalar_one_or_none()
    if not d:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Deployment not found")
    if not await has_permission(str(current_user.id), *DEPLOYMENTS_VIEW_ALL):
        if d.id not in await assigned_deployment_ids(db, current_user.id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Deployment not found")
    return d


@router.post("", response_model=DeploymentCreateOut, status_code=201)
async def create_deployment(
    body: DeploymentCreate, db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(*DEPLOYMENTS_CREATE)),
):
    """Pre-creates a pending Deployment row + single-use registration_token
    before the client's infra even exists — the token (plus this hub's URL)
    then gets set as HUB_REGISTRATION_TOKEN/HUB_URL in that deployment's own
    Dokploy Environment tab."""
    token = secrets.token_urlsafe(32)
    d = Deployment(
        client_name=body.client_name, slug=body.slug,
        registration_token_hash=hashlib.sha256(token.encode()).hexdigest(),
        status=DeploymentStatus.pending,
    )
    db.add(d)
    await db.flush()
    await db.refresh(d)
    return DeploymentCreateOut(id=d.id, client_name=d.client_name, slug=d.slug, status=d.status, registration_token=token)


@router.get("/staff-options", response_model=list[StaffOptionOut])
async def list_staff_options(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(*DEPLOYMENTS_ASSIGN_STAFF)),
):
    """Every active staff account, for the deployment-assignment picker.
    Registered ahead of GET /{deployment_id} so "staff-options" isn't
    swallowed as a deployment_id path param."""
    rows = (await db.execute(select(User).where(User.is_active.is_(True)).order_by(User.username))).scalars().all()
    return [StaffOptionOut.model_validate(u) for u in rows]


@router.get("", response_model=DeploymentListResponse)
async def list_deployments(
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(*DEPLOYMENTS_VIEW)),
):
    conditions = []
    if not await has_permission(str(current_user.id), *DEPLOYMENTS_VIEW_ALL):
        scoped_ids = await assigned_deployment_ids(db, current_user.id)
        if not scoped_ids:
            return DeploymentListResponse(total=0, items=[])
        conditions.append(Deployment.id.in_(scoped_ids))

    total = (await db.execute(select(func.count(Deployment.id)).where(*conditions))).scalar() or 0
    rows = (await db.execute(
        select(Deployment).where(*conditions).order_by(Deployment.created_at.desc())
        .offset((page - 1) * page_size).limit(page_size)
    )).scalars().all()
    active_windows = await currently_active_windows(db)
    assigned_staff_map = await _assigned_staff_map(db, [d.id for d in rows])
    return DeploymentListResponse(total=total, items=[await _to_out(db, d, active_windows, assigned_staff_map) for d in rows])


@router.get("/{deployment_id}", response_model=DeploymentOut)
async def get_deployment(
    deployment_id: str, db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(*DEPLOYMENTS_VIEW)),
):
    d = await _get_visible_or_404(db, deployment_id, current_user)
    assigned_staff_map = await _assigned_staff_map(db, [d.id])
    return await _to_out(db, d, await currently_active_windows(db), assigned_staff_map)


@router.put("/{deployment_id}/staff", response_model=list[StaffOptionOut])
async def assign_staff(
    deployment_id: str, body: DeploymentStaffAssignRequest, db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(*DEPLOYMENTS_ASSIGN_STAFF)),
):
    """Replaces this deployment's assigned-staff set wholesale — not an
    incremental add/remove. Assigning at least one person here narrows
    services/notifications/recipients.py's fan-out for this deployment's
    tickets/subscription-requests to just the assigned staff; clearing the
    set (empty user_ids) reverts to notifying every fleet-area permission
    holder — see recipients.py's fleet_staff()."""
    d = await _get_visible_or_404(db, deployment_id, current_user)
    wanted_ids = list(dict.fromkeys(body.user_ids))  # de-dupe, preserve order
    if wanted_ids:
        found = (await db.execute(select(User.id).where(User.id.in_(wanted_ids)))).scalars().all()
        missing = set(wanted_ids) - set(found)
        if missing:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown staff user id(s): {', '.join(str(m) for m in missing)}")

    await db.execute(delete(DeploymentStaffAssignment).where(DeploymentStaffAssignment.deployment_id == d.id))
    for user_id in wanted_ids:
        db.add(DeploymentStaffAssignment(deployment_id=d.id, user_id=user_id))
    await db.flush()
    assigned_staff_map = await _assigned_staff_map(db, [d.id])
    return assigned_staff_map.get(d.id, [])


@router.post("/{deployment_id}/reissue-token", response_model=ReissueTokenOut)
async def reissue_token(
    deployment_id: str, db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(*DEPLOYMENTS_CREATE)),
):
    """Manual recovery path (Phase 1, see the plan's edge-case table) for a
    deployment whose locally stored AppSetting state was lost after a
    successful registration — issues a fresh single-use token and resets
    this row back to pending. Gated by DEPLOYMENTS_CREATE, not a separate
    permission — it's the same "onboard a deployment" capability."""
    d = await _get_visible_or_404(db, deployment_id, current_user)
    token = secrets.token_urlsafe(32)
    d.registration_token_hash = hashlib.sha256(token.encode()).hexdigest()
    d.registration_token_consumed_at = None
    d.status = DeploymentStatus.pending
    await db.flush()
    return ReissueTokenOut(registration_token=token)


# ── inbound actions (hub -> deployment) ─────────────────────────────────

@router.post("/{deployment_id}/actions/renew")
async def action_renew(
    deployment_id: str, body: RenewActionRequest, db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(*DEPLOYMENTS_RENEW)),
):
    """Money action — gated by an approval workflow when one is configured
    (migration-seeded key 'deployment_renew', see app/approvals/
    deployment_hooks.py). With no active/published workflow for that key,
    behaves exactly as before: runs immediately and returns the
    deployment's raw response. When gated, returns {"approval_required":
    true, ...} instead and the real call happens once approved."""
    d = await _get_visible_or_404(db, deployment_id, current_user)
    new_expiry_date = body.new_expiry_date.isoformat()

    async def _immediate() -> dict:
        try:
            return await deployment_client.renew(d, new_expiry_date=new_expiry_date, renewal_amount=body.renewal_amount)
        except deployment_client.DeploymentCallError as e:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e))

    return await deployment_hooks.request_or_execute(
        db, current_user, key=deployment_hooks.RENEW_KEY, deployment=d,
        variables={
            "new_expiry_date": new_expiry_date, "renewal_amount": body.renewal_amount,
            "summary": f"Renew {d.client_name} to {new_expiry_date}",
        },
        immediate=_immediate,
    )


@router.post("/{deployment_id}/actions/suspend")
async def action_suspend(
    deployment_id: str, body: SuspendActionRequest, db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(*DEPLOYMENTS_SUSPEND)),
):
    """Money action — gated by an approval workflow when one is configured
    (migration-seeded key 'deployment_suspend'). See action_renew's
    docstring for the gated/immediate response shapes."""
    d = await _get_visible_or_404(db, deployment_id, current_user)

    async def _immediate() -> dict:
        try:
            return await deployment_client.suspend(d, reason=body.reason)
        except deployment_client.DeploymentCallError as e:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e))

    return await deployment_hooks.request_or_execute(
        db, current_user, key=deployment_hooks.SUSPEND_KEY, deployment=d,
        variables={"reason": body.reason, "summary": f"Suspend {d.client_name}: {body.reason}"},
        immediate=_immediate,
    )


@router.post("/{deployment_id}/actions/change-plan")
async def action_change_plan(
    deployment_id: str, body: ChangePlanActionRequest, db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(*DEPLOYMENTS_CHANGE_PLAN)),
):
    """Money action — gated by an approval workflow when one is configured
    (migration-seeded key 'deployment_change_plan'). See action_renew's
    docstring for the gated/immediate response shapes."""
    d = await _get_visible_or_404(db, deployment_id, current_user)
    new_plan_id = str(body.new_plan_id)

    async def _immediate() -> dict:
        try:
            return await deployment_client.change_plan(d, new_plan_id=new_plan_id)
        except deployment_client.DeploymentCallError as e:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e))

    return await deployment_hooks.request_or_execute(
        db, current_user, key=deployment_hooks.CHANGE_PLAN_KEY, deployment=d,
        variables={"new_plan_id": new_plan_id, "summary": f"Change plan for {d.client_name}"},
        immediate=_immediate,
    )


@router.post("/{deployment_id}/actions/extend-expiry")
async def action_extend_expiry(
    deployment_id: str, body: ExtendExpiryActionRequest, db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(*DEPLOYMENTS_EXTEND_EXPIRY)),
):
    d = await _get_visible_or_404(db, deployment_id, current_user)
    try:
        result = await deployment_client.extend_expiry(d, new_expiry_date=body.new_expiry_date.isoformat())
    except deployment_client.DeploymentCallError as e:
        record_event(
            db, event_type=et.DEPLOYMENT_EXPIRY_EXTEND_FAILED, source=et.SOURCE_HUB, actor_type=et.ACTOR_STAFF,
            actor_id=current_user.id, entity_type=et.ENTITY_DEPLOYMENT, entity_id=d.id, deployment_id=d.id,
            status=OperationalEventStatus.failure, metadata={"error": str(e)},
        )
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e))
    record_event(
        db, event_type=et.DEPLOYMENT_EXPIRY_EXTENDED, source=et.SOURCE_HUB, actor_type=et.ACTOR_STAFF,
        actor_id=current_user.id, entity_type=et.ENTITY_DEPLOYMENT, entity_id=d.id, deployment_id=d.id,
        status=OperationalEventStatus.success, metadata={"new_expiry_date": body.new_expiry_date.isoformat()},
    )
    return result


@router.post("/{deployment_id}/actions/check-health")
async def action_check_health(
    deployment_id: str, db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(*DEPLOYMENTS_CHECK_HEALTH)),
):
    """Live on-demand probe — see deployment_client.check_health's docstring
    for how this differs from the passive, heartbeat-derived `Health` dot
    on the list/detail pages."""
    d = await _get_visible_or_404(db, deployment_id, current_user)
    try:
        result = await deployment_client.check_health(d)
    except deployment_client.DeploymentCallError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e))
    record_event(
        db, event_type=et.DEPLOYMENT_HEALTH_CHECK_COMPLETED, source=et.SOURCE_HUB, actor_type=et.ACTOR_STAFF,
        actor_id=current_user.id, entity_type=et.ENTITY_DEPLOYMENT, entity_id=d.id, deployment_id=d.id,
        status=OperationalEventStatus.success if result.get("api") else OperationalEventStatus.failure,
        metadata=result,
    )
    return result


@router.patch("/{deployment_id}/subscription-requests/{request_id}")
async def review_subscription_request(
    deployment_id: str, request_id: str, body: SubscriptionRequestReviewAction, db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(*DEPLOYMENTS_REVIEW_REQUEST)),
):
    d = await _get_visible_or_404(db, deployment_id, current_user)
    try:
        result = await deployment_client.review_request(
            d, request_id=request_id, status=body.status, review_note=body.review_note,
        )
    except deployment_client.DeploymentCallError as e:
        record_event(
            db, event_type=et.DEPLOYMENT_SUBSCRIPTION_REQUEST_REVIEW_FAILED, source=et.SOURCE_HUB, actor_type=et.ACTOR_STAFF,
            actor_id=current_user.id, entity_type=et.ENTITY_DEPLOYMENT, entity_id=d.id, deployment_id=d.id,
            status=OperationalEventStatus.failure, metadata={"request_id": request_id, "error": str(e)},
        )
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e))
    record_event(
        db, event_type=et.DEPLOYMENT_SUBSCRIPTION_REQUEST_REVIEWED, source=et.SOURCE_HUB, actor_type=et.ACTOR_STAFF,
        actor_id=current_user.id, entity_type=et.ENTITY_DEPLOYMENT, entity_id=d.id, deployment_id=d.id,
        status=OperationalEventStatus.success, metadata={"request_id": request_id, "review_status": body.status},
    )
    return result


# ── deployment action executions (HUB-Expansion.md Phase 12/13 — see
# app/approvals/deployment_hooks.py) ────────────────────────────────────────

@router.get("/{deployment_id}/action-executions", response_model=list[DeploymentActionExecutionOut])
async def list_action_executions(
    deployment_id: str, db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(*DEPLOYMENTS_VIEW)),
):
    d = await _get_visible_or_404(db, deployment_id, current_user)
    rows = (await db.execute(
        select(DeploymentActionExecution).where(DeploymentActionExecution.deployment_id == d.id)
        .order_by(DeploymentActionExecution.created_at.desc())
    )).scalars().all()
    return [await _execution_out(db, e) for e in rows]


@router.post("/{deployment_id}/action-executions/{execution_id}/retry", response_model=DeploymentActionExecutionOut)
async def retry_action_execution(
    deployment_id: str, execution_id: str, db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(*ACTIONS_RETRY)),
):
    d = await _get_visible_or_404(db, deployment_id, current_user)
    execution = (await db.execute(
        select(DeploymentActionExecution).where(
            DeploymentActionExecution.id == execution_id, DeploymentActionExecution.deployment_id == d.id,
        )
    )).scalar_one_or_none()
    if execution is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Execution not found")
    execution = await deployment_hooks.retry_execution(db, current_user, execution)
    return await _execution_out(db, execution)
