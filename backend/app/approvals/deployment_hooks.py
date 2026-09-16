"""The one place deployments.py's gated action routes (renew/suspend/
change-plan) touch the workflow/approval engine — everything else about
SpiffWorkflow and the rule engine stays behind app/approvals/integration.py's
seam. Imported once from app/main.py purely for its module-load-time side
effect (hooks.register_completion_hook) — nothing here is called directly
except request_or_execute, from api/routers/deployments.py.

Whether an action is "gated" is inferred from whether an active, published
WorkflowDefinition exists for its key — this hub has no separate
AppSetting toggle table, so migration 012's seeded (and admin-editable)
`deployment_renew`/`deployment_suspend`/`deployment_change_plan`
definitions double as the on/off switch: deactivate or unpublish one and
that action goes back to running immediately.
"""
import logging
import uuid
from typing import Any, Awaitable, Callable, Dict

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.approvals import hooks, integration
from app.core import event_types as et
from app.models import Deployment, OperationalEventStatus, User
from app.services import deployment_client
from app.services.events import record_event
from app.workflow import repositories as workflow_repositories
from app.workflow import services as workflow_services
from app.workflow.models import InstanceStatus, WorkflowInstance

logger = logging.getLogger(__name__)

RENEW_KEY = "deployment_renew"
SUSPEND_KEY = "deployment_suspend"
CHANGE_PLAN_KEY = "deployment_change_plan"

# key -> (requested, executed, failed) OperationalEvent types — see
# core/event_types.py's comment on why all three gated actions share this
# shape even though HUB-Expansion.md's own example list only spells it out
# for "renew".
_EVENT_TYPES = {
    RENEW_KEY: (et.DEPLOYMENT_RENEW_REQUESTED, et.DEPLOYMENT_RENEW_EXECUTED, et.DEPLOYMENT_RENEW_FAILED),
    SUSPEND_KEY: (et.DEPLOYMENT_SUSPEND_REQUESTED, et.DEPLOYMENT_SUSPEND_EXECUTED, et.DEPLOYMENT_SUSPEND_FAILED),
    CHANGE_PLAN_KEY: (et.DEPLOYMENT_CHANGE_PLAN_REQUESTED, et.DEPLOYMENT_CHANGE_PLAN_EXECUTED, et.DEPLOYMENT_CHANGE_PLAN_FAILED),
}


async def _is_gated(db: AsyncSession, key: str) -> bool:
    try:
        definition = await workflow_services.resolve_definition_by_key(db, key)
    except Exception:
        return False
    version = await workflow_repositories.get_published_version(db, definition.id)
    return version is not None


async def request_or_execute(
    db: AsyncSession, current_user: User, *,
    key: str, deployment: Deployment, variables: Dict[str, Any], immediate: Callable[[], Awaitable[dict]],
) -> dict:
    """Either starts (and returns) an approval workflow instance, or runs
    `immediate` straight away and returns its raw result — the same shape
    the un-gated action endpoints have always returned, so a fleet with no
    approval flow configured sees no behavior change at all.

    Either way, exactly one correlation_id is generated for this call and
    threaded through every OperationalEvent it causes — the gated path
    stores it on the WorkflowInstance (see integration.start_approval) so
    the later approve/execute events can find it again; the immediate path
    just reuses it directly since everything happens in this one call."""
    requested_type, executed_type, failed_type = _EVENT_TYPES[key]
    correlation_id = uuid.uuid4()
    if await _is_gated(db, key):
        instance = await integration.start_approval(
            db, current_user, definition_key=key,
            business_object_type=key, business_object_id=deployment.id, variables=variables,
            correlation_id=correlation_id, deployment_id=deployment.id,
        )
        record_event(
            db, event_type=requested_type, source=et.SOURCE_HUB, actor_type=et.ACTOR_STAFF, actor_id=current_user.id,
            entity_type=et.ENTITY_DEPLOYMENT, entity_id=deployment.id, deployment_id=deployment.id,
            correlation_id=correlation_id, status=OperationalEventStatus.pending,
            metadata={"instance_id": str(instance.id), "variables": {k: v for k, v in variables.items() if k != "summary"}},
        )
        return {
            "approval_required": True,
            "instance_id": str(instance.id),
            "instance_code": instance.instance_code,
            "status": instance.status.value,
            "message": (
                "Approval requested — this action will run once approved."
                if instance.status == InstanceStatus.running
                else f"Workflow finished immediately: {instance.status.value}."
            ),
        }
    try:
        result = await immediate()
    except HTTPException:
        record_event(
            db, event_type=failed_type, source=et.SOURCE_HUB, actor_type=et.ACTOR_STAFF, actor_id=current_user.id,
            entity_type=et.ENTITY_DEPLOYMENT, entity_id=deployment.id, deployment_id=deployment.id,
            correlation_id=correlation_id, status=OperationalEventStatus.failure,
        )
        raise
    record_event(
        db, event_type=executed_type, source=et.SOURCE_HUB, actor_type=et.ACTOR_STAFF, actor_id=current_user.id,
        entity_type=et.ENTITY_DEPLOYMENT, entity_id=deployment.id, deployment_id=deployment.id,
        correlation_id=correlation_id, status=OperationalEventStatus.success,
    )
    return result


async def _variables_map(db: AsyncSession, instance_id) -> Dict[str, Any]:
    rows = await workflow_repositories.list_variables_for_instance(db, instance_id)
    return {r.name: r.value for r in rows}


def _record_execution_result(
    db: AsyncSession, *, key: str, instance: WorkflowInstance, deployment: Deployment, error: Exception | None,
) -> None:
    """Emits the domain-specific executed/failed event once the completion
    hook actually knows the outcome of the deferred deployment_client call
    — the generic approval.approved event (approvals/hooks.py's
    fire_if_terminal) only records that the *approval* succeeded, which is
    a different, earlier fact than whether the call it unblocked actually
    reached the deployment. See HUB-Expansion.md Phase 12: this doesn't
    fix the underlying "approved but execution failed" gap (still just
    logged, no retry, no EXECUTION_PENDING/EXECUTING state) — it only
    makes the failure visible in the event log instead of only in
    application logs."""
    _, executed_type, failed_type = _EVENT_TYPES[key]
    if error is None:
        record_event(
            db, event_type=executed_type, source=et.SOURCE_ENGINE, actor_type=et.ACTOR_SYSTEM,
            entity_type=et.ENTITY_DEPLOYMENT, entity_id=deployment.id, deployment_id=deployment.id,
            correlation_id=instance.correlation_id or instance.id, status=OperationalEventStatus.success,
            metadata={"instance_id": str(instance.id)},
        )
    else:
        record_event(
            db, event_type=failed_type, source=et.SOURCE_ENGINE, actor_type=et.ACTOR_SYSTEM,
            entity_type=et.ENTITY_DEPLOYMENT, entity_id=deployment.id, deployment_id=deployment.id,
            correlation_id=instance.correlation_id or instance.id, status=OperationalEventStatus.failure,
            metadata={"instance_id": str(instance.id), "error": str(error)},
        )


async def _on_renew_complete(db: AsyncSession, instance: WorkflowInstance) -> None:
    if instance.status != InstanceStatus.completed:
        return
    deployment = await db.get(Deployment, instance.business_object_id)
    if deployment is None:
        return
    variables = await _variables_map(db, instance.id)
    try:
        await deployment_client.renew(
            deployment, new_expiry_date=variables["new_expiry_date"], renewal_amount=variables.get("renewal_amount"),
        )
    except deployment_client.DeploymentCallError as e:
        logger.warning("Approved renew for deployment %s failed to reach the deployment", deployment.id, exc_info=True)
        _record_execution_result(db, key=RENEW_KEY, instance=instance, deployment=deployment, error=e)
    else:
        _record_execution_result(db, key=RENEW_KEY, instance=instance, deployment=deployment, error=None)


async def _on_suspend_complete(db: AsyncSession, instance: WorkflowInstance) -> None:
    if instance.status != InstanceStatus.completed:
        return
    deployment = await db.get(Deployment, instance.business_object_id)
    if deployment is None:
        return
    variables = await _variables_map(db, instance.id)
    try:
        await deployment_client.suspend(deployment, reason=variables.get("reason") or "")
    except deployment_client.DeploymentCallError as e:
        logger.warning("Approved suspend for deployment %s failed to reach the deployment", deployment.id, exc_info=True)
        _record_execution_result(db, key=SUSPEND_KEY, instance=instance, deployment=deployment, error=e)
    else:
        _record_execution_result(db, key=SUSPEND_KEY, instance=instance, deployment=deployment, error=None)


async def _on_change_plan_complete(db: AsyncSession, instance: WorkflowInstance) -> None:
    if instance.status != InstanceStatus.completed:
        return
    deployment = await db.get(Deployment, instance.business_object_id)
    if deployment is None:
        return
    variables = await _variables_map(db, instance.id)
    try:
        await deployment_client.change_plan(deployment, new_plan_id=variables["new_plan_id"])
    except deployment_client.DeploymentCallError as e:
        logger.warning("Approved change-plan for deployment %s failed to reach the deployment", deployment.id, exc_info=True)
        _record_execution_result(db, key=CHANGE_PLAN_KEY, instance=instance, deployment=deployment, error=e)
    else:
        _record_execution_result(db, key=CHANGE_PLAN_KEY, instance=instance, deployment=deployment, error=None)


hooks.register_completion_hook(RENEW_KEY, _on_renew_complete)
hooks.register_completion_hook(SUSPEND_KEY, _on_suspend_complete)
hooks.register_completion_hook(CHANGE_PLAN_KEY, _on_change_plan_complete)
