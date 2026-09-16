"""Where the fleet events are wired into services/notifications/ — two
called from api/routers/ingest.py, one from core/expiry_reminder_scheduler.py.
Kept out of those call sites so they stay focused on their own job; see
services/notifications/README.md's "Triggers" section for the full picture.

All three fan out via recipients_for_deployment() — the deployment's
explicitly assigned staff (api/routers/deployments.py's PUT .../staff) if
any, otherwise every user holding a fleet-area permission (recipients.py's
fleet_staff()). One email per recipient with an email on file, one
WhatsApp message per recipient with a phone on file. A recipient with
neither is simply skipped for both (same "never block the caller" posture
as a missing address anywhere else in this package)."""
import logging
from datetime import date
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import Deployment, SupportTicket
from app.schemas import PendingRequestIn
from app.services.notifications.recipients import recipients_for_deployment
from app.services.notifications.service import NotificationService

logger = logging.getLogger(__name__)


def _deployment_url(deployment_id) -> str:
    """Links to the deployment detail page, which shows the subscription
    snapshot (including pending_requests) directly — the right destination
    for a subscription-request alert."""
    return f"{settings.FRONTEND_URL.rstrip('/')}/deployments/{deployment_id}"


def _tickets_url(deployment_id) -> str:
    """Links to the fleet-wide tickets list pre-filtered to this deployment
    (SupportTicketsPage reads ?deployment_id= — see App.tsx) — the deployment
    detail page itself doesn't show tickets, so a support-ticket alert must
    not link there."""
    return f"{settings.FRONTEND_URL.rstrip('/')}/tickets?deployment_id={deployment_id}"


async def notify_support_ticket_raised(db: AsyncSession, ticket: SupportTicket, deployment: Deployment) -> None:
    recipients = await recipients_for_deployment(db, deployment.id)
    if not recipients:
        return
    service = NotificationService(db)
    url = _tickets_url(deployment.id)
    for user in recipients:
        if user.email:
            try:
                await service.send_support_ticket_alert(
                    recipient=user.email,
                    client_name=deployment.client_name,
                    subject=ticket.subject,
                    description=ticket.description,
                    priority=ticket.priority,
                    submitted_by_name=ticket.submitted_by_name,
                    submitted_by_email=ticket.submitted_by_email,
                    ticket_url=url,
                )
            except Exception:
                logger.warning("Failed to queue support-ticket email to %s", user.email, exc_info=True)
        if user.phone:
            try:
                await service.send_support_ticket_whatsapp(
                    recipient=user.phone,
                    client_name=deployment.client_name,
                    subject=ticket.subject,
                    priority=ticket.priority,
                    ticket_url=url,
                )
            except Exception:
                logger.warning("Failed to queue support-ticket WhatsApp message to %s", user.phone, exc_info=True)


async def notify_subscription_request_raised(
    db: AsyncSession, request: PendingRequestIn, deployment: Deployment,
) -> None:
    recipients = await recipients_for_deployment(db, deployment.id)
    if not recipients:
        return
    service = NotificationService(db)
    url = _deployment_url(deployment.id)
    for user in recipients:
        if user.email:
            try:
                await service.send_subscription_request_alert(
                    recipient=user.email,
                    client_name=deployment.client_name,
                    request_type=request.request_type,
                    requested_plan_name=request.requested_plan_name,
                    message=request.message,
                    deployment_url=url,
                )
            except Exception:
                logger.warning("Failed to queue subscription-request email to %s", user.email, exc_info=True)
        if user.phone:
            try:
                await service.send_subscription_request_whatsapp(
                    recipient=user.phone,
                    client_name=deployment.client_name,
                    request_type=request.request_type,
                    requested_plan_name=request.requested_plan_name,
                    deployment_url=url,
                )
            except Exception:
                logger.warning("Failed to queue subscription-request WhatsApp message to %s", user.phone, exc_info=True)


async def notify_subscription_expiring(db: AsyncSession, deployment: Deployment, expiry_date: date, days_left: int) -> None:
    """Called from core/expiry_reminder_scheduler.py once per deployment
    entering its expiry-reminder window — that scheduler owns the
    idempotency check (Deployment.expiry_reminder_sent_for), this function
    just sends."""
    recipients = await recipients_for_deployment(db, deployment.id)
    if not recipients:
        return
    service = NotificationService(db)
    url = _deployment_url(deployment.id)
    expiry_str = expiry_date.isoformat()
    for user in recipients:
        if user.email:
            try:
                await service.send_subscription_expiring_alert(
                    recipient=user.email,
                    client_name=deployment.client_name,
                    expiry_date=expiry_str,
                    days_left=days_left,
                    deployment_url=url,
                )
            except Exception:
                logger.warning("Failed to queue subscription-expiring email to %s", user.email, exc_info=True)
        if user.phone:
            try:
                await service.send_subscription_expiring_whatsapp(
                    recipient=user.phone,
                    client_name=deployment.client_name,
                    expiry_date=expiry_str,
                    days_left=days_left,
                    deployment_url=url,
                )
            except Exception:
                logger.warning("Failed to queue subscription-expiring WhatsApp message to %s", user.phone, exc_info=True)


def new_pending_requests(
    previous: Optional[list[dict]], incoming: list[PendingRequestIn],
) -> list[PendingRequestIn]:
    """Diff a heartbeat's pending_requests against the immediately-preceding
    snapshot's — pending_requests is this deployment's full current list on
    every heartbeat, not an event, so an unchanged request must never
    re-notify every 2 hours. `previous` is DeploymentSnapshot.pending_requests
    (raw JSON, i.e. a list of dicts) from the prior snapshot, or None for a
    deployment's very first heartbeat (nothing to diff against — no alert)."""
    if previous is None:
        return []
    previous_ids = {r.get("id") for r in previous if isinstance(r, dict)}
    return [r for r in incoming if r.id not in previous_ids]
