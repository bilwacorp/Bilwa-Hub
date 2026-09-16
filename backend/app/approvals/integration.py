"""The only two functions another module (deployments, ...) needs to
import to add an approval step — everything else about SpiffWorkflow and
the rule engine stays behind this seam. Ported from PoultryPro-CBF's
app/approvals/integration.py with branch_id stripped."""
import uuid
from typing import Any, Dict, Optional

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.approvals import hooks
from app.models import User
from app.workflow import executor, repositories, services as workflow_services
from app.workflow.models import WorkflowInstance


async def start_approval(
    db: AsyncSession, current_user: User, *,
    definition_key: str, business_object_type: str, business_object_id: uuid.UUID,
    variables: Dict[str, Any],
) -> WorkflowInstance:
    """Start a workflow instance against a business object. Raises 500 if
    the named workflow has no active definition or no published version —
    a module wiring in approvals should treat that as a deployment/config
    error, not something to work around. Callers that want to gate this on
    "is a workflow even configured for this key" should check that first
    (see app/approvals/deployment_hooks.py's _active_definition_or_none)
    and fall back to immediate execution instead of calling this at all."""
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
    # Covers the edge case of a process with no human tasks at all (every
    # step auto-approves/skips) completing synchronously on start, before
    # any approve/reject request ever fires the hook.
    await hooks.fire_if_terminal(db, instance)
    return instance


async def get_approval_status(db: AsyncSession, business_object_type: str, business_object_id: uuid.UUID) -> Optional[WorkflowInstance]:
    """The most recent workflow instance run against this business object,
    or None if none has ever been started."""
    return await repositories.get_instance_for_object(db, business_object_type, business_object_id)
