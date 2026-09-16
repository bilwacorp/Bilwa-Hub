"""Async CRUD for NotificationLog. Kept separate from service.py so the
Celery task (which drives its own short-lived DB session — see tasks.py) and
the admin API router can both depend on plain data-access functions instead
of duplicating queries."""

import json
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import and_, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import NotificationChannel, NotificationLog, NotificationStatus


async def create_log(
    db: AsyncSession,
    *,
    channel: NotificationChannel,
    provider: str,
    recipient: str,
    subject: Optional[str] = None,
    template: Optional[str] = None,
    payload: Optional[dict] = None,
) -> NotificationLog:
    log = NotificationLog(
        channel=channel,
        provider=provider,
        recipient=recipient,
        subject=subject,
        template=template,
        payload=json.dumps(payload) if payload is not None else None,
        status=NotificationStatus.pending,
    )
    db.add(log)
    await db.flush()
    await db.refresh(log)
    return log


async def get_by_id(db: AsyncSession, notification_id: uuid.UUID) -> Optional[NotificationLog]:
    return (await db.execute(
        select(NotificationLog).where(NotificationLog.id == notification_id)
    )).scalar_one_or_none()


async def mark_sending(db: AsyncSession, log: NotificationLog) -> None:
    log.status = NotificationStatus.sending
    log.updated_at = datetime.utcnow()
    await db.flush()


async def mark_sent(db: AsyncSession, log: NotificationLog) -> None:
    log.status = NotificationStatus.sent
    log.sent_at = datetime.utcnow()
    log.updated_at = datetime.utcnow()
    log.error_message = None
    await db.flush()


async def mark_failed(db: AsyncSession, log: NotificationLog, error_message: str, *, increment_retry: bool = True) -> None:
    log.status = NotificationStatus.failed
    log.error_message = error_message[:2000]
    log.updated_at = datetime.utcnow()
    if increment_retry:
        log.retry_count += 1
    await db.flush()


async def reset_for_resend(db: AsyncSession, log: NotificationLog) -> None:
    log.status = NotificationStatus.pending
    log.error_message = None
    log.updated_at = datetime.utcnow()
    await db.flush()


async def list_logs(
    db: AsyncSession,
    *,
    page: int = 1,
    page_size: int = 50,
    status: Optional[NotificationStatus] = None,
    channel: Optional[NotificationChannel] = None,
    recipient: Optional[str] = None,
) -> tuple[int, list[NotificationLog]]:
    filters = []
    if status:
        filters.append(NotificationLog.status == status)
    if channel:
        filters.append(NotificationLog.channel == channel)
    if recipient:
        filters.append(NotificationLog.recipient.ilike(f"%{recipient.strip()}%"))

    where_clause = and_(*filters) if filters else None

    count_q = select(func.count()).select_from(NotificationLog)
    if where_clause is not None:
        count_q = count_q.where(where_clause)
    total = (await db.execute(count_q)).scalar_one()

    q = select(NotificationLog).order_by(NotificationLog.created_at.desc())
    if where_clause is not None:
        q = q.where(where_clause)
    q = q.offset((page - 1) * page_size).limit(page_size)

    items = (await db.execute(q)).scalars().all()
    return total, list(items)


async def delete_log(db: AsyncSession, log: NotificationLog) -> None:
    await db.execute(delete(NotificationLog).where(NotificationLog.id == log.id))
