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
from typing import Any, Awaitable, Callable, Dict

from sqlalchemy.ext.asyncio import AsyncSession

from app.approvals import hooks, integration
from app.models import Deployment, User
from app.services import deployment_client
from app.workflow import repositories as workflow_repositories
from app.workflow import services as workflow_services
from app.workflow.models import InstanceStatus, WorkflowInstance

logger = logging.getLogger(__name__)

RENEW_KEY = "deployment_renew"
SUSPEND_KEY = "deployment_suspend"
CHANGE_PLAN_KEY = "deployment_change_plan"


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
    approval flow configured sees no behavior change at all."""
    if await _is_gated(db, key):
        instance = await integration.start_approval(
            db, current_user, definition_key=key,
            business_object_type=key, business_object_id=deployment.id, variables=variables,
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
    return await immediate()


async def _variables_map(db: AsyncSession, instance_id) -> Dict[str, Any]:
    rows = await workflow_repositories.list_variables_for_instance(db, instance_id)
    return {r.name: r.value for r in rows}


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
    except deployment_client.DeploymentCallError:
        logger.warning("Approved renew for deployment %s failed to reach the deployment", deployment.id, exc_info=True)


async def _on_suspend_complete(db: AsyncSession, instance: WorkflowInstance) -> None:
    if instance.status != InstanceStatus.completed:
        return
    deployment = await db.get(Deployment, instance.business_object_id)
    if deployment is None:
        return
    variables = await _variables_map(db, instance.id)
    try:
        await deployment_client.suspend(deployment, reason=variables.get("reason") or "")
    except deployment_client.DeploymentCallError:
        logger.warning("Approved suspend for deployment %s failed to reach the deployment", deployment.id, exc_info=True)


async def _on_change_plan_complete(db: AsyncSession, instance: WorkflowInstance) -> None:
    if instance.status != InstanceStatus.completed:
        return
    deployment = await db.get(Deployment, instance.business_object_id)
    if deployment is None:
        return
    variables = await _variables_map(db, instance.id)
    try:
        await deployment_client.change_plan(deployment, new_plan_id=variables["new_plan_id"])
    except deployment_client.DeploymentCallError:
        logger.warning("Approved change-plan for deployment %s failed to reach the deployment", deployment.id, exc_info=True)


hooks.register_completion_hook(RENEW_KEY, _on_renew_complete)
hooks.register_completion_hook(SUSPEND_KEY, _on_suspend_complete)
hooks.register_completion_hook(CHANGE_PLAN_KEY, _on_change_plan_complete)
