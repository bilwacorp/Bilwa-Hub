"""Admin visibility into services/notifications/ — history, resend, delete,
and real test sends. Gated by NOTIFICATIONS_MANAGE — see core/permissions.py."""
import json
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import NOTIFICATIONS_MANAGE, require_permission
from app.db.session import get_db
from app.models import NotificationChannel, NotificationStatus
from app.schemas import (
    NotificationLogListResponse, NotificationLogOut, NotificationSendResponse, TestEmailRequest, TestWhatsAppRequest,
)
from app.services.notifications import repository
from app.services.notifications.constants import SENSITIVE_CONTEXT_KEYS
from app.services.notifications.dependencies import get_notification_service
from app.services.notifications.service import NotificationService
from app.services.notifications.tasks import send_email_task, send_whatsapp_task

router = APIRouter(
    prefix="/notifications", tags=["notifications"],
    dependencies=[Depends(require_permission(*NOTIFICATIONS_MANAGE))],
)


async def _get_log_or_404(db: AsyncSession, notification_id: uuid.UUID):
    log = await repository.get_by_id(db, notification_id)
    if log is None:
        raise HTTPException(status_code=404, detail="Notification not found")
    return log


@router.get("", response_model=NotificationLogListResponse)
@router.get("/history", response_model=NotificationLogListResponse)
async def list_notifications(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    status: Optional[NotificationStatus] = None,
    channel: Optional[NotificationChannel] = None,
    recipient: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    total, items = await repository.list_logs(
        db, page=page, page_size=page_size, status=status, channel=channel, recipient=recipient
    )
    return NotificationLogListResponse(total=total, items=items)


@router.post("/test-email", response_model=NotificationSendResponse)
async def send_test_email(
    body: TestEmailRequest,
    service: NotificationService = Depends(get_notification_service),
):
    log = await service.send_custom(
        recipient=body.recipient,
        subject="BilwaCorp Fleet Hub — test email",
        # Any existing template works for a connectivity test; the fields
        # it doesn't use are just left blank in the rendered copy.
        template="support_ticket_raised",
        context={
            "client_name": "Test Client",
            "ticket_subject": "This is a test email",
            "description": "If you're reading this, SMTP delivery is configured correctly.",
            "priority": "normal",
            "submitted_by_name": None,
            "submitted_by_email": None,
            "ticket_url": None,
        },
    )
    return NotificationSendResponse(notification_id=log.id, status=log.status)


@router.post("/test-whatsapp", response_model=NotificationSendResponse)
async def send_test_whatsapp(
    body: TestWhatsAppRequest,
    service: NotificationService = Depends(get_notification_service),
):
    log = await service.send_whatsapp_message(recipient=body.recipient, message=body.message)
    return NotificationSendResponse(notification_id=log.id, status=log.status)


@router.post("/resend/{notification_id}", response_model=NotificationSendResponse)
async def resend_notification(
    notification_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    log = await _get_log_or_404(db, notification_id)
    context = json.loads(log.payload) if log.payload else {}

    if log.channel == NotificationChannel.whatsapp and not log.template:
        # A raw-text WhatsApp send (NotificationService.send_whatsapp_message)
        # has no template to re-render — the sanitized payload's own
        # "message" field (never sensitive-redacted; it's plain content, not
        # a secret) is enough to resend directly.
        message = context.get("message")
        if not message:
            raise HTTPException(status_code=400, detail="This notification has no content recorded and cannot be resent")
        await repository.reset_for_resend(db, log)
        await db.commit()
        send_whatsapp_task.delay(str(log.id), log.recipient, message)
        return NotificationSendResponse(notification_id=log.id, status=log.status)

    if not log.template:
        raise HTTPException(status_code=400, detail="This notification has no template recorded and cannot be resent")
    if any(key in context for key in SENSITIVE_CONTEXT_KEYS):
        raise HTTPException(
            status_code=400,
            detail="This notification carried a one-time secret that isn't retained — trigger a fresh send instead of resending.",
        )
    await repository.reset_for_resend(db, log)
    await db.commit()

    if log.channel == NotificationChannel.whatsapp:
        send_whatsapp_task.delay(str(log.id), log.recipient, None, log.template, context)
    else:
        send_email_task.delay(str(log.id), [log.recipient], log.subject or "", log.template, context, [], [])
    return NotificationSendResponse(notification_id=log.id, status=log.status)


@router.get("/{notification_id}", response_model=NotificationLogOut)
async def get_notification(
    notification_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    return await _get_log_or_404(db, notification_id)


@router.delete("/{notification_id}")
async def delete_notification(
    notification_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    log = await _get_log_or_404(db, notification_id)
    await repository.delete_log(db, log)
    await db.commit()
    return {"deleted": True}
