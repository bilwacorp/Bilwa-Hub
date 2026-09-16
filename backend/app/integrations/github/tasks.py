"""Celery tasks for the GitHub integration — registered on the SAME
celery_app instance services/notifications/tasks.py already defines (see
that module's bottom-of-file import of this one, and
docs/integrations/github.md's "Celery task registration" section for why
that's necessary rather than running a second app/worker)."""
import asyncio
import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from app.db.session import make_celery_sessionmaker
from app.integrations.github import sync, webhooks
from app.integrations.github.client import GitHubApiError, GitHubConnectionError, GitHubRateLimitError
from app.integrations.github.models import GitHubIntegration, GitHubIntegrationStatus, GitHubRepository, GitHubWebhookEvent
from app.services.notifications.tasks import celery_app

logger = logging.getLogger(__name__)

# See db/session.py's make_celery_sessionmaker docstring — a dedicated
# NullPool sessionmaker for this module, not shared with notifications'.
CelerySessionLocal = make_celery_sessionmaker()


def _run_async(coro):
    """Mirrors services/notifications/tasks.py's own _run_async — see its
    docstring. Not imported from there directly: it's a tiny, generic
    asyncio-in-celery shim, not a GitHub- or notifications-specific
    concept, so each Celery tasks module keeping its own copy is cheap
    duplication of glue code, not of a domain concept."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    else:
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()


async def _process_webhook_event(webhook_event_id: str) -> None:
    async with CelerySessionLocal() as db:
        webhook_event = await db.get(GitHubWebhookEvent, uuid.UUID(webhook_event_id))
        if webhook_event is None:
            logger.warning("process_webhook_event_task: GitHubWebhookEvent %s not found, skipping", webhook_event_id)
            return
        integration = await db.get(GitHubIntegration, webhook_event.integration_id)
        try:
            await webhooks.process_webhook_event(db, webhook_event, integration)
        except (GitHubRateLimitError, GitHubConnectionError):
            await db.commit()  # keep the "failed" status + error this attempt recorded
            raise
        except GitHubApiError:
            await db.commit()
            return  # permanent failure — don't retry, but don't crash the worker either
        await db.commit()


@celery_app.task(
    bind=True,
    name="github.process_webhook_event",
    autoretry_for=(GitHubRateLimitError, GitHubConnectionError),
    retry_backoff=True,
    retry_backoff_max=120,
    retry_jitter=True,
    max_retries=5,
)
def process_webhook_event_task(self, webhook_event_id: str) -> None:
    _run_async(_process_webhook_event(webhook_event_id))


async def _sync_repository(repository_id: str) -> None:
    async with CelerySessionLocal() as db:
        repository = await db.get(GitHubRepository, uuid.UUID(repository_id))
        if repository is None:
            logger.warning("sync_repository_task: GitHubRepository %s not found, skipping", repository_id)
            return
        integration = await db.get(GitHubIntegration, repository.integration_id)
        try:
            await sync.sync_repository(db, repository, integration)
        except GitHubApiError as e:
            integration.status = GitHubIntegrationStatus.error
            integration.last_error = str(e)[:2000]
            integration.last_error_at = datetime.utcnow()
            await db.commit()
            if isinstance(e, (GitHubRateLimitError, GitHubConnectionError)):
                raise
            return
        await db.commit()


@celery_app.task(
    bind=True,
    name="github.sync_repository",
    autoretry_for=(GitHubRateLimitError, GitHubConnectionError),
    retry_backoff=True,
    retry_backoff_max=120,
    retry_jitter=True,
    max_retries=5,
)
def sync_repository_task(self, repository_id: str) -> None:
    _run_async(_sync_repository(repository_id))
