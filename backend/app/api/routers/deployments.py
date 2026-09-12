"""Staff-facing deployment registry + inbound-action triggers. Every route
requires FLEET_MANAGE (see core/permissions.py — granted to both 'admin'
and 'engineer')."""
import hashlib
import secrets
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.permissions import FLEET_MANAGE, require_permission
from app.db.session import get_db
from app.models import Deployment, DeploymentSnapshot, DeploymentStatus, MaintenanceWindow, User
from app.schemas import (
    ChangePlanActionRequest, DeploymentCreate, DeploymentCreateOut, DeploymentListResponse,
    DeploymentOut, DeploymentSnapshotOut, ExtendExpiryActionRequest, ReissueTokenOut,
    RenewActionRequest, SubscriptionRequestReviewAction, SuspendActionRequest,
)
from app.services import deployment_client
from app.services.maintenance_query import currently_active_windows

router = APIRouter(prefix="/deployments", tags=["deployments"], dependencies=[Depends(require_permission(*FLEET_MANAGE))])


async def _latest_snapshot(db: AsyncSession, deployment_id) -> DeploymentSnapshot | None:
    return (await db.execute(
        select(DeploymentSnapshot).where(DeploymentSnapshot.deployment_id == deployment_id)
        .order_by(DeploymentSnapshot.received_at.desc()).limit(1)
    )).scalar_one_or_none()


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


async def _to_out(db: AsyncSession, d: Deployment, active_windows: list[MaintenanceWindow]) -> DeploymentOut:
    snap = await _latest_snapshot(db, d.id)
    out = DeploymentOut.model_validate(d)
    out.latest_snapshot = DeploymentSnapshotOut.model_validate(snap) if snap else None
    out.heartbeat_age_seconds, out.derived_status = _derive_status(d, snap, active_windows)
    return out


async def _get_or_404(db: AsyncSession, deployment_id: str) -> Deployment:
    d = (await db.execute(select(Deployment).where(Deployment.id == deployment_id))).scalar_one_or_none()
    if not d:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Deployment not found")
    return d


@router.post("", response_model=DeploymentCreateOut, status_code=201)
async def create_deployment(body: DeploymentCreate, db: AsyncSession = Depends(get_db)):
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


@router.get("", response_model=DeploymentListResponse)
async def list_deployments(
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), db: AsyncSession = Depends(get_db),
):
    total = (await db.execute(select(func.count(Deployment.id)))).scalar() or 0
    rows = (await db.execute(
        select(Deployment).order_by(Deployment.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    )).scalars().all()
    active_windows = await currently_active_windows(db)
    return DeploymentListResponse(total=total, items=[await _to_out(db, d, active_windows) for d in rows])


@router.get("/{deployment_id}", response_model=DeploymentOut)
async def get_deployment(deployment_id: str, db: AsyncSession = Depends(get_db)):
    d = await _get_or_404(db, deployment_id)
    return await _to_out(db, d, await currently_active_windows(db))


@router.post("/{deployment_id}/reissue-token", response_model=ReissueTokenOut)
async def reissue_token(deployment_id: str, db: AsyncSession = Depends(get_db)):
    """Manual recovery path (Phase 1, see the plan's edge-case table) for a
    deployment whose locally stored AppSetting state was lost after a
    successful registration — issues a fresh single-use token and resets
    this row back to pending."""
    d = await _get_or_404(db, deployment_id)
    token = secrets.token_urlsafe(32)
    d.registration_token_hash = hashlib.sha256(token.encode()).hexdigest()
    d.registration_token_consumed_at = None
    d.status = DeploymentStatus.pending
    await db.flush()
    return ReissueTokenOut(registration_token=token)


# ── inbound actions (hub -> deployment) ─────────────────────────────────

@router.post("/{deployment_id}/actions/renew")
async def action_renew(deployment_id: str, body: RenewActionRequest, db: AsyncSession = Depends(get_db)):
    d = await _get_or_404(db, deployment_id)
    try:
        return await deployment_client.renew(d, new_expiry_date=body.new_expiry_date.isoformat(), renewal_amount=body.renewal_amount)
    except deployment_client.DeploymentCallError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e))


@router.post("/{deployment_id}/actions/suspend")
async def action_suspend(deployment_id: str, body: SuspendActionRequest, db: AsyncSession = Depends(get_db)):
    d = await _get_or_404(db, deployment_id)
    try:
        return await deployment_client.suspend(d, reason=body.reason)
    except deployment_client.DeploymentCallError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e))


@router.post("/{deployment_id}/actions/change-plan")
async def action_change_plan(deployment_id: str, body: ChangePlanActionRequest, db: AsyncSession = Depends(get_db)):
    d = await _get_or_404(db, deployment_id)
    try:
        return await deployment_client.change_plan(d, new_plan_id=str(body.new_plan_id))
    except deployment_client.DeploymentCallError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e))


@router.post("/{deployment_id}/actions/extend-expiry")
async def action_extend_expiry(deployment_id: str, body: ExtendExpiryActionRequest, db: AsyncSession = Depends(get_db)):
    d = await _get_or_404(db, deployment_id)
    try:
        return await deployment_client.extend_expiry(d, new_expiry_date=body.new_expiry_date.isoformat())
    except deployment_client.DeploymentCallError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e))


@router.post("/{deployment_id}/actions/check-health")
async def action_check_health(deployment_id: str, db: AsyncSession = Depends(get_db)):
    """Live on-demand probe — see deployment_client.check_health's docstring
    for how this differs from the passive, heartbeat-derived `Health` dot
    on the list/detail pages."""
    d = await _get_or_404(db, deployment_id)
    try:
        return await deployment_client.check_health(d)
    except deployment_client.DeploymentCallError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e))


@router.patch("/{deployment_id}/subscription-requests/{request_id}")
async def review_subscription_request(
    deployment_id: str, request_id: str, body: SubscriptionRequestReviewAction, db: AsyncSession = Depends(get_db),
):
    d = await _get_or_404(db, deployment_id)
    try:
        return await deployment_client.review_request(
            d, request_id=request_id, status=body.status, review_note=body.review_note,
        )
    except deployment_client.DeploymentCallError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e))
