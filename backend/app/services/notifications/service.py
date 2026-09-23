"""Central entry point for sending notifications. Every send_* method creates
a NotificationLog row (status=pending) and enqueues a Celery task — nothing
here ever sends an email/WhatsApp message inline. Business code (routers,
other services) should only ever import NotificationService, never a
provider or the Celery task directly.

Ported from PoultryPro-CBF's services/notifications/service.py, trimmed to
this hub's two channels (no push) and its simpler env-var kill switches (no
AppSetting DB toggle — see core/config.py's NOTIFICATIONS_ENABLED /
WHATSAPP_NOTIFICATIONS_ENABLED)."""

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import NotificationChannel, NotificationLog, NotificationStatus
from app.services.notifications import repository
from app.services.notifications.constants import (
    DEFAULT_PROVIDER,
    DEFAULT_WHATSAPP_PROVIDER,
    SENSITIVE_CONTEXT_KEYS,
    TEMPLATE_APPROVAL_REQUESTED,
    TEMPLATE_SUBSCRIPTION_EXPIRING,
    TEMPLATE_SUBSCRIPTION_REQUEST_RAISED,
    TEMPLATE_SUPPORT_TICKET_RAISED,
)
from app.services.notifications.tasks import send_email_task, send_whatsapp_task

logger = logging.getLogger(__name__)


def _sanitize(context: dict) -> dict:
    return {k: ("***" if k in SENSITIVE_CONTEXT_KEYS else v) for k, v in context.items()}


def _base_context(context: dict) -> dict:
    return {
        "company_name": settings.FROM_NAME,
        "current_year": datetime.now(timezone.utc).year,
        **context,
    }


def _notifications_enabled(channel: NotificationChannel = NotificationChannel.email) -> bool:
    """Global kill switch (env-only, see core/config.py). WhatsApp has its
    own narrower switch layered underneath the global one — both must be on
    for a WhatsApp send to actually enqueue; email is unaffected either way."""
    if not settings.NOTIFICATIONS_ENABLED:
        return False
    if channel == NotificationChannel.whatsapp and not settings.WHATSAPP_NOTIFICATIONS_ENABLED:
        return False
    return True


class NotificationService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def send_template(
        self,
        *,
        recipient: str,
        template: str,
        context: dict,
        subject: str,
        cc: Optional[list[str]] = None,
        bcc: Optional[list[str]] = None,
    ) -> NotificationLog:
        full_context = _base_context(context)

        log = await repository.create_log(
            self.db,
            channel=NotificationChannel.email,
            provider=DEFAULT_PROVIDER,
            recipient=recipient,
            subject=subject,
            template=template,
            payload=_sanitize(full_context),
        )

        if not _notifications_enabled():
            # Still record that a send was attempted (for visibility in the
            # admin notifications list) but never enqueue it — the kill
            # switch is off. Callers always get a NotificationLog back.
            log.status = NotificationStatus.cancelled
            await self.db.flush()
            return log

        # Flush (not commit) — callers routinely use NotificationService from
        # inside a larger unit of work (e.g. ingest_support_ticket, which
        # relies on get_db's commit-after-return) that isn't done yet.
        # Committing here would end their transaction early; not committing
        # at all would mean the Celery task's own DB lookup can't see this
        # row yet. Instead, enqueue only once *this* session's transaction
        # actually commits — so a caller that rolls back never triggers a
        # send for a log row that no longer exists.
        def _enqueue(_session) -> None:
            try:
                send_email_task.delay(str(log.id), [recipient], subject, template, full_context, cc or [], bcc or [])
            except Exception:
                # An unreachable broker must never fail the business
                # transaction that triggered this notification — that
                # transaction's own commit has *already succeeded* by the
                # time this runs. The log row is left pending, same as if
                # the worker were simply down; it can be resent later via
                # POST /notifications/resend/{id}.
                logger.warning("Failed to enqueue notification %s — it will stay pending", log.id, exc_info=True)

        event.listen(self.db.sync_session, "after_commit", _enqueue, once=True)
        return log

    # send_email is the low-level alias — identical to send_template, kept
    # as a separate name for callers that think in terms of "send an email"
    # rather than "send a template".
    async def send_email(self, **kwargs) -> NotificationLog:
        return await self.send_template(**kwargs)

    async def send_custom(self, *, recipient: str, subject: str, template: str, context: dict) -> NotificationLog:
        """One-off email against any existing template file — for the admin
        test-email endpoint."""
        return await self.send_template(recipient=recipient, template=template, context=context, subject=subject)

    async def send_whatsapp_message(self, *, recipient: str, message: str) -> NotificationLog:
        """Direct plain-text WhatsApp send — the low-level entry point for
        one-off alerts that don't need a shared template. `recipient` is a
        phone number, not an email address."""
        log = await repository.create_log(
            self.db,
            channel=NotificationChannel.whatsapp,
            provider=DEFAULT_WHATSAPP_PROVIDER,
            recipient=recipient,
            subject=message[:255],
            template=None,
            payload=_sanitize({"message": message}),
        )

        if not _notifications_enabled(channel=NotificationChannel.whatsapp):
            log.status = NotificationStatus.cancelled
            await self.db.flush()
            return log

        def _enqueue(_session) -> None:
            try:
                send_whatsapp_task.delay(str(log.id), recipient, message)
            except Exception:
                logger.warning("Failed to enqueue WhatsApp notification %s — it will stay pending", log.id, exc_info=True)

        event.listen(self.db.sync_session, "after_commit", _enqueue, once=True)
        return log

    async def send_whatsapp_template(self, *, recipient: str, template: str, context: dict) -> NotificationLog:
        """Send a purpose-built WhatsApp text template (templates_whatsapp/)
        — short-form copy written for this channel, not the email templates
        stripped down to text. `recipient` is a phone number."""
        full_context = _base_context(context)
        log = await repository.create_log(
            self.db,
            channel=NotificationChannel.whatsapp,
            provider=DEFAULT_WHATSAPP_PROVIDER,
            recipient=recipient,
            subject=None,
            template=template,
            payload=_sanitize(full_context),
        )

        if not _notifications_enabled(channel=NotificationChannel.whatsapp):
            log.status = NotificationStatus.cancelled
            await self.db.flush()
            return log

        def _enqueue(_session) -> None:
            try:
                send_whatsapp_task.delay(str(log.id), recipient, None, template, full_context)
            except Exception:
                logger.warning("Failed to enqueue WhatsApp notification %s — it will stay pending", log.id, exc_info=True)

        event.listen(self.db.sync_session, "after_commit", _enqueue, once=True)
        return log

    # ── convenience methods — one per notification type ─────────────────

    async def send_support_ticket_alert(
        self,
        *,
        recipient: str,
        client_name: str,
        subject: str,
        description: str,
        priority: str,
        submitted_by_name: Optional[str],
        submitted_by_email: Optional[str],
        ticket_url: Optional[str] = None,
    ) -> NotificationLog:
        return await self.send_template(
            recipient=recipient,
            template=TEMPLATE_SUPPORT_TICKET_RAISED,
            context={
                "client_name": client_name,
                "ticket_subject": subject,
                "description": description,
                "priority": priority,
                "submitted_by_name": submitted_by_name,
                "submitted_by_email": submitted_by_email,
                "ticket_url": ticket_url,
            },
            subject=f"[{client_name}] New support ticket: {subject}",
        )

    async def send_support_ticket_whatsapp(
        self, *, recipient: str, client_name: str, subject: str, priority: str, ticket_url: Optional[str] = None,
    ) -> NotificationLog:
        return await self.send_whatsapp_template(
            recipient=recipient,
            template=TEMPLATE_SUPPORT_TICKET_RAISED,
            context={"client_name": client_name, "ticket_subject": subject, "priority": priority, "ticket_url": ticket_url},
        )

    async def send_subscription_request_alert(
        self,
        *,
        recipient: str,
        client_name: str,
        request_type: str,
        requested_plan_name: Optional[str],
        message: Optional[str],
        deployment_url: Optional[str] = None,
    ) -> NotificationLog:
        return await self.send_template(
            recipient=recipient,
            template=TEMPLATE_SUBSCRIPTION_REQUEST_RAISED,
            context={
                "client_name": client_name,
                "request_type": request_type,
                "requested_plan_name": requested_plan_name,
                "message": message,
                "deployment_url": deployment_url,
            },
            subject=f"[{client_name}] New {request_type.replace('_', ' ')} request",
        )

    async def send_subscription_request_whatsapp(
        self, *, recipient: str, client_name: str, request_type: str, requested_plan_name: Optional[str], deployment_url: Optional[str] = None,
    ) -> NotificationLog:
        return await self.send_whatsapp_template(
            recipient=recipient,
            template=TEMPLATE_SUBSCRIPTION_REQUEST_RAISED,
            context={
                "client_name": client_name,
                "request_type": request_type,
                "requested_plan_name": requested_plan_name,
                "deployment_url": deployment_url,
            },
        )

    async def send_subscription_expiring_alert(
        self, *, recipient: str, client_name: str, expiry_date: str, days_left: int, deployment_url: Optional[str] = None,
    ) -> NotificationLog:
        return await self.send_template(
            recipient=recipient,
            template=TEMPLATE_SUBSCRIPTION_EXPIRING,
            context={
                "client_name": client_name,
                "expiry_date": expiry_date,
                "days_left": days_left,
                "deployment_url": deployment_url,
            },
            subject=f"[{client_name}] Subscription expires in {days_left} day{'s' if days_left != 1 else ''}",
        )

    async def send_subscription_expiring_whatsapp(
        self, *, recipient: str, client_name: str, expiry_date: str, days_left: int, deployment_url: Optional[str] = None,
    ) -> NotificationLog:
        return await self.send_whatsapp_template(
            recipient=recipient,
            template=TEMPLATE_SUBSCRIPTION_EXPIRING,
            context={
                "client_name": client_name,
                "expiry_date": expiry_date,
                "days_left": days_left,
                "deployment_url": deployment_url,
            },
        )

    async def send_approval(
        self, *, recipient: str, approver: str, request: str, action_url: str,
    ) -> NotificationLog:
        """A new workflow approval task was assigned to `recipient` — see
        app/workflow/executor.py's _notify_candidate_approvers. Email only:
        this hub has no push/mobile app to notify a candidate approver on."""
        return await self.send_template(
            recipient=recipient,
            template=TEMPLATE_APPROVAL_REQUESTED,
            context={"approver": approver, "request": request, "action_url": action_url},
            subject=f"Approval needed: {request}",
        )

