"""HUB-Expansion.md Phase 11 — Integration Center (Settings -> Integrations).
Read-only rollup over data every earlier phase already produces — no new
tables, and never returns a secret (GitHubIntegration's own token/secret
columns are never selected here, same as api/routers github.py's own
GitHubIntegrationOut). GitHub gets one card per configured integration
(or a single "not configured" placeholder); CI/CD, Email, WhatsApp, and
Monitoring are each a single card since this hub supports at most one of
each today."""
from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.permissions import INTEGRATIONS_VIEW, require_permission
from app.db.session import get_db
from app.integrations.github.models import GitHubIntegration
from app.models import DeploymentRelease, DeploymentReleaseSource, NotificationChannel, NotificationLog, NotificationStatus, User
from app.schemas import IntegrationSummaryOut

router = APIRouter(prefix="/integrations", tags=["integrations"])


async def _github_cards(db: AsyncSession) -> list[IntegrationSummaryOut]:
    rows = (await db.execute(select(GitHubIntegration).order_by(GitHubIntegration.created_at))).scalars().all()
    if not rows:
        return [IntegrationSummaryOut(
            key="github", name="GitHub", status="not_configured", connected=False,
            detail="No GitHub integration configured yet.",
        )]
    return [
        IntegrationSummaryOut(
            key=f"github:{r.id}", name=r.name, status=r.status.value, connected=r.status.value == "connected",
            last_sync_at=r.last_synced_at, last_webhook_at=r.last_webhook_at,
            last_error=r.last_error, last_error_at=r.last_error_at, integration_id=r.id,
        )
        for r in rows
    ]


async def _cicd_card(db: AsyncSession) -> IntegrationSummaryOut:
    last_deploy_at = (await db.execute(
        select(func.max(DeploymentRelease.deployed_at)).where(
            DeploymentRelease.source.in_([DeploymentReleaseSource.github_actions, DeploymentReleaseSource.ci_cd])
        )
    )).scalar()
    return IntegrationSummaryOut(
        key="cicd", name="CI/CD", status="connected" if last_deploy_at else "not_configured",
        connected=last_deploy_at is not None, last_sync_at=last_deploy_at,
        detail="Rides the same GitHub webhook as the GitHub integration above — see docs/adr/ADR-005-cicd-integration.md.",
    )


async def _channel_card(db: AsyncSession, *, key: str, name: str, channel: NotificationChannel, configured: bool, detail: str) -> IntegrationSummaryOut:
    if not configured:
        return IntegrationSummaryOut(key=key, name=name, status="not_configured", connected=False, detail=detail)
    last_success_at = (await db.execute(
        select(func.max(NotificationLog.sent_at)).where(NotificationLog.channel == channel, NotificationLog.status == NotificationStatus.sent)
    )).scalar()
    last_failure = (await db.execute(
        select(NotificationLog).where(NotificationLog.channel == channel, NotificationLog.status == NotificationStatus.failed)
        .order_by(NotificationLog.created_at.desc()).limit(1)
    )).scalar_one_or_none()
    is_error = last_failure is not None and (last_success_at is None or last_failure.created_at > last_success_at)
    return IntegrationSummaryOut(
        key=key, name=name, status="error" if is_error else "connected", connected=not is_error,
        last_sync_at=last_success_at, last_error=last_failure.error_message if last_failure else None,
        last_error_at=last_failure.created_at if last_failure else None, detail=detail,
    )


@router.get("", response_model=list[IntegrationSummaryOut])
async def list_integrations(
    db: AsyncSession = Depends(get_db), current_user: User = Depends(require_permission(*INTEGRATIONS_VIEW)),
):
    email = await _channel_card(
        db, key="email", name="Email", channel=NotificationChannel.email, configured=bool(settings.SMTP_HOST),
        detail="No SMTP credentials on file." if not settings.SMTP_HOST else None,
    )
    whatsapp = await _channel_card(
        db, key="whatsapp", name="WhatsApp", channel=NotificationChannel.whatsapp, configured=bool(settings.WHATSAPP_API_URL),
        detail="No WhatsApp gateway configured." if not settings.WHATSAPP_API_URL else None,
    )
    monitoring = IntegrationSummaryOut(
        key="monitoring", name="Monitoring", status="not_configured", connected=False,
        detail="No monitoring integration exists yet.",
    )
    return [*(await _github_cards(db)), await _cicd_card(db), email, whatsapp, monitoring]
