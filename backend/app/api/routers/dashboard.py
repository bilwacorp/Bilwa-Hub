"""HUB-Expansion.md Phase 10 — Operations Dashboard. One read-only
endpoint; see app/services/dashboard.py for how every number is
computed and which sections quietly zero out for a caller missing that
domain's own view permission."""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import DASHBOARD_VIEW, DEPLOYMENTS_VIEW_ALL, has_permission, require_permission
from app.db.session import get_db
from app.models import User
from app.schemas import DashboardOut
from app.services.dashboard import build_dashboard
from app.services.deployment_scope import assigned_deployment_ids

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("", response_model=DashboardOut)
async def get_dashboard(
    db: AsyncSession = Depends(get_db), current_user: User = Depends(require_permission(*DASHBOARD_VIEW)),
):
    deployment_ids = None
    if not await has_permission(str(current_user.id), *DEPLOYMENTS_VIEW_ALL):
        deployment_ids = list(await assigned_deployment_ids(db, current_user.id))
    return await build_dashboard(db, current_user, deployment_ids)
