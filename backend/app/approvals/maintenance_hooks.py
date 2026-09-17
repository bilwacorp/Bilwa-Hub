"""HUB-Expansion.md Phase 8's "expand the workflow engine's use carefully"
applied to Phase 7's maintenance lifecycle — the second business domain
to use app/approvals/integration.py's start_approval/hooks.py's
register_completion_hook, after app/approvals/deployment_hooks.py. This
file is deliberately much shorter than that one: a maintenance window has
no deferred external call to retry (deployment_hooks.py's
DeploymentActionExecution/Attempt machinery exists because a REAL HTTP
call to a client deployment can fail independently of the approval
itself) — here, "the action" is just a status transition + the
already-existing push machinery (services/maintenance_push.py), which the
scheduler already retries every tick regardless of approval history.

Whether a submission is gated is the same "active, published
WorkflowDefinition for this key" inference deployment_hooks.py uses — see
migration 018's seeded (and admin-editable) `maintenance_window_approval`
definition, which doubles as the on/off switch."""
import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.approvals import hooks, integration
from app.core import event_types as et
from app.models import MaintenanceWindow, MaintenanceWindowStatus, OperationalEventStatus, User
from app.services.events import record_event
from app.workflow import repositories as workflow_repositories
from app.workflow import services as workflow_services
from app.workflow.models import InstanceStatus, WorkflowInstance, WorkflowTaskStatus

logger = logging.getLogger(__name__)

MAINTENANCE_APPROVAL_KEY = "maintenance_window_approval"


def _is_high_risk(window: MaintenanceWindow) -> bool:
    """A window needs approval only if it's actually high-stakes — a
    plain single-deployment banner/read_only window (the overwhelming
    common case, and everything this hub supported before Phase 7) is
    NEVER gated, keeping "existing behavior must remain compatible" true
    for the case that matters. High-risk = fleet-wide (deployment_id is
    NULL) OR the most severe enforcement level (lockout)."""
    return window.deployment_id is None or window.mode == "lockout"


async def _is_gated(db: AsyncSession) -> bool:
    try:
        definition = await workflow_services.resolve_definition_by_key(db, MAINTENANCE_APPROVAL_KEY)
    except Exception:
        return False
    version = await workflow_repositories.get_published_version(db, definition.id)
    return version is not None


async def route_window(db: AsyncSession, current_user: User, window: MaintenanceWindow) -> None:
    """Called from api/routers/maintenance.py on create (unless
    save_as_draft) and on POST .../submit. Decides `window.status` —
    either straight to `planned` (ungated, or not high-risk), or
    `approval_required` with a workflow instance started against it.
    Mutates window in place; caller is responsible for flushing."""
    if _is_high_risk(window) and await _is_gated(db):
        instance = await integration.start_approval(
            db, current_user, definition_key=MAINTENANCE_APPROVAL_KEY,
            business_object_type=MAINTENANCE_APPROVAL_KEY, business_object_id=window.id,
            variables={
                "window_id": str(window.id), "mode": window.mode,
                "summary": f"Maintenance window ({window.mode}) {'fleet-wide' if window.deployment_id is None else window.deployment_id}",
            },
            deployment_id=window.deployment_id,
        )
        window.status = MaintenanceWindowStatus.approval_required
        record_event(
            db, event_type=et.MAINTENANCE_APPROVAL_REQUESTED, source=et.SOURCE_HUB, actor_type=et.ACTOR_STAFF,
            actor_id=current_user.id, entity_type=et.ENTITY_MAINTENANCE_WINDOW, entity_id=window.id,
            deployment_id=window.deployment_id, correlation_id=instance.correlation_id,
            metadata={"instance_id": str(instance.id)},
        )
        # Same synchronous-auto-complete-on-start edge case
        # integration.start_approval's own docstring calls out — the
        # completion hook below may already have run by the time we
        # regain control, so window.status may already be past
        # approval_required here.
    else:
        window.status = MaintenanceWindowStatus.planned
    await db.flush()


async def _window_for_instance(db: AsyncSession, instance: WorkflowInstance) -> Optional[MaintenanceWindow]:
    return await db.get(MaintenanceWindow, instance.business_object_id)


async def _approver_id(db: AsyncSession, instance_id) -> Optional[str]:
    tasks = await workflow_repositories.list_tasks_for_instance(db, instance_id)
    acted = [t for t in tasks if t.status == WorkflowTaskStatus.approved and t.acted_by]
    return acted[-1].acted_by if acted else None


async def _on_maintenance_approval_complete(db: AsyncSession, instance: WorkflowInstance) -> None:
    window = await _window_for_instance(db, instance)
    if window is None:
        return

    if instance.status == InstanceStatus.completed:
        window.status = MaintenanceWindowStatus.approved
        window.approved_by = await _approver_id(db, instance.id)
        record_event(
            db, event_type=et.MAINTENANCE_APPROVED, source=et.SOURCE_ENGINE, actor_type=et.ACTOR_SYSTEM,
            entity_type=et.ENTITY_MAINTENANCE_WINDOW, entity_id=window.id, deployment_id=window.deployment_id,
            correlation_id=instance.correlation_id or instance.id, status=OperationalEventStatus.success,
        )
    elif instance.status in (InstanceStatus.rejected, InstanceStatus.cancelled):
        # Back to draft — the submitter can revise and resubmit rather
        # than the window being stuck in approval_required forever.
        window.status = MaintenanceWindowStatus.draft
        record_event(
            db, event_type=et.MAINTENANCE_APPROVAL_REJECTED, source=et.SOURCE_ENGINE, actor_type=et.ACTOR_SYSTEM,
            entity_type=et.ENTITY_MAINTENANCE_WINDOW, entity_id=window.id, deployment_id=window.deployment_id,
            correlation_id=instance.correlation_id or instance.id, status=OperationalEventStatus.success,
            metadata={"instance_status": instance.status.value},
        )
    else:  # InstanceStatus.error — no approver rule resolved; a config problem, not a business outcome
        window.status = MaintenanceWindowStatus.failed
        record_event(
            db, event_type=et.MAINTENANCE_FAILED, source=et.SOURCE_ENGINE, actor_type=et.ACTOR_SYSTEM,
            entity_type=et.ENTITY_MAINTENANCE_WINDOW, entity_id=window.id, deployment_id=window.deployment_id,
            correlation_id=instance.correlation_id or instance.id, status=OperationalEventStatus.failure,
            metadata={"reason": "approval routing error"},
        )
    await db.flush()


hooks.register_completion_hook(MAINTENANCE_APPROVAL_KEY, _on_maintenance_approval_complete)
