"""The one place deployments.py's gated action routes (renew/suspend/
change-plan) touch the workflow/approval engine — everything else about
SpiffWorkflow and the rule engine stays behind app/approvals/integration.py's
seam. Imported once from app/main.py purely for its module-load-time side
effect (hooks.register_completion_hook) — nothing here is called directly
except request_or_execute and retry_execution, from api/routers/deployments.py.

Whether an action is "gated" is inferred from whether an active, published
WorkflowDefinition exists for its key — this hub has no separate
AppSetting toggle table, so migration 012's seeded (and admin-editable)
`deployment_renew`/`deployment_suspend`/`deployment_change_plan`
definitions double as the on/off switch: deactivate or unpublish one and
that action goes back to running immediately.

HUB-Expansion.md Phase 12/13: previously, once an approval completed, the
real deployment_client call ran synchronously inside the SAME request that
approved the task, and a failure was only ever `logger.warning`'d — the
approving staff member's HTTP response showed a successful workflow
instance with no indication the actual call to the deployment failed.
DeploymentActionExecution (app/models.py) now tracks that call's own state
(pending/executing/executed/failed) independently of the approval's own
state, with attempt history and a retry endpoint
(POST /deployments/{id}/action-executions/{id}/retry) — see retry_execution
below. The call itself is still made synchronously (within the approve
request, or within the retry request); this fixes the "invisible failure"
gap, not the separate "should this be dispatched to Celery instead"
question, which is deliberately out of scope here.
"""
import logging
import uuid
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, Optional

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.approvals import hooks, integration
from app.core import event_types as et
from app.models import (
    Deployment, DeploymentActionAttempt, DeploymentActionAttemptStatus, DeploymentActionExecution,
    DeploymentActionExecutionStatus, OperationalEventStatus, User,
)
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

# key -> the actual deployment_client call, given (deployment, variables,
# idempotency_key). Keeping this as data (rather than three near-identical
# completion-hook functions) is what lets _execute() below be one function
# shared by retry and by every action's completion hook.
_CLIENT_CALLS: Dict[str, Callable[[Deployment, Dict[str, Any], str], Awaitable[dict]]] = {
    RENEW_KEY: lambda d, v, idem: deployment_client.renew(
        d, new_expiry_date=v["new_expiry_date"], renewal_amount=v.get("renewal_amount"), idempotency_key=idem,
    ),
    SUSPEND_KEY: lambda d, v, idem: deployment_client.suspend(d, reason=v.get("reason") or "", idempotency_key=idem),
    CHANGE_PLAN_KEY: lambda d, v, idem: deployment_client.change_plan(d, new_plan_id=v["new_plan_id"], idempotency_key=idem),
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
    stores it on the WorkflowInstance and the new DeploymentActionExecution
    row (see integration.start_approval) so the later approve/execute/retry
    events can find it again; the immediate path just reuses it directly
    since everything happens in this one call."""
    requested_type, executed_type, failed_type = _EVENT_TYPES[key]
    correlation_id = uuid.uuid4()
    if await _is_gated(db, key):
        instance = await integration.start_approval(
            db, current_user, definition_key=key,
            business_object_type=key, business_object_id=deployment.id, variables=variables,
            correlation_id=correlation_id, deployment_id=deployment.id,
        )
        execution = DeploymentActionExecution(
            workflow_instance_id=instance.id, deployment_id=deployment.id, action_key=key,
            idempotency_key=f"hub-action-{instance.id}", correlation_id=correlation_id,
        )
        db.add(execution)
        await db.flush()
        record_event(
            db, event_type=requested_type, source=et.SOURCE_HUB, actor_type=et.ACTOR_STAFF, actor_id=current_user.id,
            entity_type=et.ENTITY_DEPLOYMENT, entity_id=deployment.id, deployment_id=deployment.id,
            correlation_id=correlation_id, status=OperationalEventStatus.pending,
            metadata={"instance_id": str(instance.id), "variables": {k: v for k, v in variables.items() if k != "summary"}},
        )
        # An instance can already be terminal by the time we get here (the
        # "no human tasks, auto-approved on start" edge case — see
        # integration.start_approval's docstring) — in that case its
        # completion hook (_on_gated_action_complete below) already ran
        # _execute() before this function regains control, so `execution`
        # may already be executed/failed here, not just pending.
        return {
            "approval_required": True,
            "instance_id": str(instance.id),
            "instance_code": instance.instance_code,
            "execution_id": str(execution.id),
            "execution_status": execution.status.value,
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


async def _execute(db: AsyncSession, execution: DeploymentActionExecution, *, triggered_by: Optional[uuid.UUID]) -> None:
    """Runs (or re-runs) the deferred deployment_client call for one
    DeploymentActionExecution. This is the one place that call actually
    happens — both the automatic first attempt (from a completion hook,
    triggered_by=None) and every manual retry (retry_execution below) go
    through here, so they get identical state transitions, attempt
    logging, and idempotency-key handling.

    A no-op (HUB-Expansion.md Phase 13: "never blindly repeat money-moving
    actions") if `execution.status` is already `executing` (an attempt is
    already in flight — callers are expected to have already checked this,
    this is belt-and-suspenders) or `executed` (already succeeded)."""
    if execution.status in (DeploymentActionExecutionStatus.executing, DeploymentActionExecutionStatus.executed):
        return
    deployment = await db.get(Deployment, execution.deployment_id)
    if deployment is None:
        return
    variables = await _variables_map(db, execution.workflow_instance_id)

    execution.status = DeploymentActionExecutionStatus.executing
    execution.attempt_count += 1
    execution.last_attempted_at = datetime.utcnow()
    await db.flush()

    started_at = execution.last_attempted_at
    attempt_number = execution.attempt_count
    call = _CLIENT_CALLS[execution.action_key]
    _, executed_type, failed_type = _EVENT_TYPES[execution.action_key]
    actor_type = et.ACTOR_STAFF if triggered_by else et.ACTOR_SYSTEM
    source = et.SOURCE_HUB if triggered_by else et.SOURCE_ENGINE
    correlation_id = execution.correlation_id or execution.id

    try:
        response = await call(deployment, variables, execution.idempotency_key)
    except deployment_client.DeploymentCallError as e:
        execution.status = DeploymentActionExecutionStatus.failed
        execution.last_error = str(e)
        db.add(DeploymentActionAttempt(
            execution_id=execution.id, attempt_number=attempt_number, status=DeploymentActionAttemptStatus.failure,
            error=str(e), triggered_by=triggered_by, started_at=started_at, finished_at=datetime.utcnow(),
        ))
        record_event(
            db, event_type=failed_type, source=source, actor_type=actor_type, actor_id=triggered_by,
            entity_type=et.ENTITY_DEPLOYMENT, entity_id=deployment.id, deployment_id=deployment.id,
            correlation_id=correlation_id, status=OperationalEventStatus.failure,
            metadata={"execution_id": str(execution.id), "attempt": attempt_number, "error": str(e)},
        )
        logger.warning(
            "Deployment action %s (execution %s, attempt %d) failed to reach deployment %s",
            execution.action_key, execution.id, attempt_number, deployment.id, exc_info=True,
        )
    else:
        execution.status = DeploymentActionExecutionStatus.executed
        execution.last_error = None
        execution.last_response = response
        db.add(DeploymentActionAttempt(
            execution_id=execution.id, attempt_number=attempt_number, status=DeploymentActionAttemptStatus.success,
            response=response, triggered_by=triggered_by, started_at=started_at, finished_at=datetime.utcnow(),
        ))
        record_event(
            db, event_type=executed_type, source=source, actor_type=actor_type, actor_id=triggered_by,
            entity_type=et.ENTITY_DEPLOYMENT, entity_id=deployment.id, deployment_id=deployment.id,
            correlation_id=correlation_id, status=OperationalEventStatus.success,
            metadata={"execution_id": str(execution.id), "attempt": attempt_number},
        )
    await db.flush()


async def get_execution_for_instance(db: AsyncSession, workflow_instance_id: uuid.UUID) -> Optional[DeploymentActionExecution]:
    return (await db.execute(
        select(DeploymentActionExecution).where(DeploymentActionExecution.workflow_instance_id == workflow_instance_id)
    )).scalar_one_or_none()


async def retry_execution(db: AsyncSession, current_user: User, execution: DeploymentActionExecution) -> DeploymentActionExecution:
    """Called from api/routers/deployments.py's retry endpoint
    (ACTIONS_RETRY-gated). Only a `failed` execution may be retried — not
    `pending` (nothing approved yet), `executing` (already in flight), or
    `executed` (already succeeded; retrying it would risk the exact
    duplicate-execution Phase 13 warns against)."""
    if execution.status != DeploymentActionExecutionStatus.failed:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Only a failed execution can be retried (current status: {execution.status.value}).",
        )
    record_event(
        db, event_type=et.DEPLOYMENT_ACTION_RETRIED, source=et.SOURCE_HUB, actor_type=et.ACTOR_STAFF,
        actor_id=current_user.id, entity_type=et.ENTITY_DEPLOYMENT, entity_id=execution.deployment_id,
        deployment_id=execution.deployment_id, correlation_id=execution.correlation_id or execution.id,
        metadata={"execution_id": str(execution.id), "attempt": execution.attempt_count + 1},
    )
    await _execute(db, execution, triggered_by=current_user.id)
    return execution


async def _on_gated_action_complete(db: AsyncSession, instance: WorkflowInstance) -> None:
    if instance.status != InstanceStatus.completed:
        return
    execution = await get_execution_for_instance(db, instance.id)
    if execution is None:
        return
    await _execute(db, execution, triggered_by=None)


hooks.register_completion_hook(RENEW_KEY, _on_gated_action_complete)
hooks.register_completion_hook(SUSPEND_KEY, _on_gated_action_complete)
hooks.register_completion_hook(CHANGE_PLAN_KEY, _on_gated_action_complete)
