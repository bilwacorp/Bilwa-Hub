"""Celery app + the background tasks that actually render and send an email
or WhatsApp message. NotificationService (service.py) never sends inline —
it creates a NotificationLog row and calls send_email_task.delay(...) /
send_whatsapp_task.delay(...); this module does the real work asynchronously,
in the celery-worker container (see docker-compose.yml)."""

import asyncio
import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from celery import Celery
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.db.session import _db_url
from app.services.notifications import repository
from app.services.notifications.exceptions import (
    NotificationException, SMTPConnectionException, WhatsAppConnectionException,
)
from app.services.notifications.providers.base import OutboundMessage
from app.services.notifications.providers.smtp import SMTPProvider
from app.services.notifications.providers.whatsapp import WhatsAppProvider
from app.services.notifications.render import render_template, render_whatsapp_template

logger = logging.getLogger(__name__)

# A dedicated engine for the Celery worker, separate from the app's pooled
# engine in db/session.py. _run_async below calls asyncio.run() per task, so
# every task gets a brand-new event loop — but asyncpg connections are bound
# to the loop that created them. A real connection pool would hand a task a
# connection created on a previous (now-closed) task's loop, breaking with
# "attached to a different loop". NullPool means every checkout is a fresh
# connection and every checkin closes it, so no connection ever crosses an
# event loop boundary.
_celery_engine = create_async_engine(
    _db_url,
    echo=False,
    poolclass=NullPool,
    connect_args={"ssl": settings.DB_SSL_MODE, "statement_cache_size": 0},
)
CelerySessionLocal = async_sessionmaker(
    _celery_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)

celery_app = Celery(
    "notifications",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
)
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    # Bound worst-case latency when publishing a task (i.e. NotificationService
    # calling .delay()) if the broker is unreachable — e.g. local dev without
    # Redis running. Celery's defaults can otherwise block the calling HTTP
    # request for 10+ seconds before failing. This only bounds the *client*
    # publish path; the worker's own connection settings are separate.
    broker_connection_timeout=2,
    task_publish_retry=False,
)


async def _send_and_record(
    log_id: str,
    to: list[str],
    subject: str,
    template_name: str,
    context: dict,
    cc: list[str],
    bcc: list[str],
) -> None:
    log_uuid = uuid.UUID(log_id)

    async with CelerySessionLocal() as db:
        log = await repository.get_by_id(db, log_uuid)
        if log is None:
            logger.warning("send_email_task: NotificationLog %s not found, skipping", log_id)
            return
        await repository.mark_sending(db, log)
        await db.commit()

    provider = SMTPProvider()
    async with CelerySessionLocal() as db:
        log = await repository.get_by_id(db, log_uuid)
        try:
            html, text = render_template(template_name, context)
            message = OutboundMessage(to=to, subject=subject, html_body=html, text_body=text, cc=cc, bcc=bcc)
            await provider.send(message)
        except NotificationException as e:
            await repository.mark_failed(db, log, str(e), increment_retry=isinstance(e, SMTPConnectionException))
            await db.commit()
            raise
        else:
            await repository.mark_sent(db, log)
            await db.commit()


@celery_app.task(
    bind=True,
    name="notifications.send_email",
    autoretry_for=(SMTPConnectionException,),
    retry_backoff=True,
    retry_backoff_max=60,
    retry_jitter=True,
    max_retries=3,
)
def send_email_task(
    self,
    log_id: str,
    to: list[str],
    subject: str,
    template: str,
    context: dict,
    cc: Optional[list[str]] = None,
    bcc: Optional[list[str]] = None,
) -> None:
    _run_async(_send_and_record(log_id, to, subject, template, context, cc or [], bcc or []))


async def _send_whatsapp_and_record(
    log_id: str,
    to: str,
    message: Optional[str],
    template: Optional[str],
    context: Optional[dict],
) -> None:
    log_uuid = uuid.UUID(log_id)

    async with CelerySessionLocal() as db:
        log = await repository.get_by_id(db, log_uuid)
        if log is None:
            logger.warning("send_whatsapp_task: NotificationLog %s not found, skipping", log_id)
            return
        await repository.mark_sending(db, log)
        await db.commit()

    text = message
    if template:
        # Purpose-built short-form WhatsApp copy (templates_whatsapp/*.txt),
        # not the HTML email templates stripped down to text — see
        # render_whatsapp_template's docstring.
        text = render_whatsapp_template(template, context or {})

    provider = WhatsAppProvider()
    async with CelerySessionLocal() as db:
        log = await repository.get_by_id(db, log_uuid)
        try:
            out_message = OutboundMessage(to=[to], subject=log.subject or "", text_body=text)
            await provider.send(out_message)
        except NotificationException as e:
            await repository.mark_failed(db, log, str(e), increment_retry=isinstance(e, WhatsAppConnectionException))
            await db.commit()
            raise
        else:
            await repository.mark_sent(db, log)
            await db.commit()


@celery_app.task(
    bind=True,
    name="notifications.send_whatsapp",
    autoretry_for=(WhatsAppConnectionException,),
    retry_backoff=True,
    retry_backoff_max=60,
    retry_jitter=True,
    max_retries=3,
)
def send_whatsapp_task(
    self,
    log_id: str,
    to: str,
    message: Optional[str] = None,
    template: Optional[str] = None,
    context: Optional[dict] = None,
) -> None:
    _run_async(_send_whatsapp_and_record(log_id, to, message, template, context))


def _run_async(coro):
    """A real Celery worker process never has a running event loop, so
    asyncio.run() is normally safe here directly. The one exception is
    task_always_eager mode (local debugging, tests), which runs the task
    inline wherever .delay() was called — if that happens to be inside an
    already-async context (e.g. a FastAPI request handler), asyncio.run()
    would raise. Falling back to a dedicated thread covers that case too."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    else:
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()
