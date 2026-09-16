"""The only two functions another module (deployments, ...) needs to
import to add an approval step — everything else about SpiffWorkflow and
the rule engine stays behind this seam. Ported from PoultryPro-CBF's
app/approvals/integration.py with branch_id stripped."""
import uuid
from typing import Any, Dict, Optional

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.approvals import hooks
from app.core import event_types as et
from app.models import User
from app.services.events import record_event
from app.workflow import executor, repositories, services as workflow_services
from app.workflow.models import WorkflowInstance


async def start_approval(
    db: AsyncSession, current_user: User, *,
    definition_key: str, business_object_type: str, business_object_id: uuid.UUID,
    variables: Dict[str, Any],
    correlation_id: Optional[uuid.UUID] = None,
    deployment_id: Optional[uuid.UUID] = None,
) -> WorkflowInstance:
    """Start a workflow instance against a business object. Raises 500 if
    the named workflow has no active definition or no published version —
    a module wiring in approvals should treat that as a deployment/config
    error, not something to work around. Callers that want to gate this on
    "is a workflow even configured for this key" should check that first
    (see app/approvals/deployment_hooks.py's _active_definition_or_none)
    and fall back to immediate execution instead of calling this at all.

    correlation_id, when the caller already has one for this operation
    (e.g. deployment_hooks.py's request_or_execute), is stored on the
    instance so every later OperationalEvent tied to it — approve/reject,
    and the eventual deployment_client call — can share it. deployment_id,
    when given, is stamped on the "approval.requested" event so a fleet/
    deployment-scoped event query finds it even though the generic
    fire_if_terminal() completion event (see approvals/hooks.py) can't
    assume business_object_id is always a deployment id."""
    try:
        definition = await workflow_services.resolve_definition_by_key(db, definition_key)
    except HTTPException as exc:
        raise HTTPException(status_code=500, detail=exc.detail) from exc
    version = await repositories.get_published_version(db, definition.id)
    if version is None:
        raise HTTPException(status_code=500, detail=f"Workflow '{definition_key}' has no published version.")
    instance = await executor.start_instance(
        db, current_user, definition=definition, version=version,
        business_object_type=business_object_type, business_object_id=business_object_id, variables=variables,
    )
    instance.correlation_id = correlation_id or uuid.uuid4()
    record_event(
        db, event_type=et.APPROVAL_REQUESTED, source=et.SOURCE_HUB, actor_type=et.ACTOR_STAFF,
        actor_id=current_user.id, entity_type=et.ENTITY_WORKFLOW_INSTANCE, entity_id=instance.id,
        deployment_id=deployment_id, correlation_id=instance.correlation_id,
        metadata={"definition_key": definition_key, "business_object_type": business_object_type, "business_object_id": str(business_object_id)},
    )
    await db.flush()
    # Covers the edge case of a process with no human tasks at all (every
    # step auto-approves/skips) completing synchronously on start, before
    # any approve/reject request ever fires the hook.
    await hooks.fire_if_terminal(db, instance, actor=current_user)
    return instance


async def get_approval_status(db: AsyncSession, business_object_type: str, business_object_id: uuid.UUID) -> Optional[WorkflowInstance]:
    """The most recent workflow instance run against this business object,
    or None if none has ever been started."""
    return await repositories.get_instance_for_object(db, business_object_type, business_object_id)
