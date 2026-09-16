"""Workflow definition/version CRUD and instance read/cancel endpoints.
Rule CRUD lives in app/rules/api.py; task actions live in
app/approvals/api.py — this file is the design-time + instance-lifecycle
surface. Ported from PoultryPro-CBF's app/workflow/api.py with branch
context stripped, and no "start any workflow instance" / "edit instance
variable" endpoints — in this hub, instances only ever start via
app/approvals/integration.start_approval, called from the deployment
action routes (see app/approvals/deployment_hooks.py), never directly
through this API."""
import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.approvals import hooks
from app.core.permissions import (
    require_permission,
    WORKFLOWS_VIEW, WORKFLOWS_CREATE, WORKFLOWS_UPDATE, WORKFLOWS_PUBLISH, WORKFLOWS_ARCHIVE, WORKFLOWS_DELETE,
    WORKFLOW_INSTANCES_VIEW, WORKFLOW_INSTANCES_CANCEL,
)
from app.models import User
from app.workflow import repositories, services
from app.workflow.parser import list_process_ids
from app.workflow.schemas import (
    ImportBpmnRequest, ImportBpmnResult, TaskActionRequest, ValidationResultOut, ValidationStepOut,
    WorkflowDefinitionCreate, WorkflowDefinitionOut, WorkflowDefinitionUpdate,
    WorkflowHistoryOut, WorkflowInstanceOut, WorkflowVariableOut,
    WorkflowVersionCreate, WorkflowVersionListOut, WorkflowVersionOut,
)

router = APIRouter(prefix="/workflows", tags=["workflows"])
instances_router = APIRouter(prefix="/workflow-instances", tags=["workflow-instances"])


def _validation_out(result) -> ValidationResultOut:
    return ValidationResultOut(
        is_valid=result.is_valid,
        errors=result.errors,
        steps=[ValidationStepOut(task_id=s.task_id, name=s.name, step_key=s.step_key, rule_key=s.rule_key) for s in result.steps],
    )


# ── Definitions ──────────────────────────────────────────────────────────

@router.get("", response_model=List[WorkflowDefinitionOut])
async def list_workflows(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(*WORKFLOWS_VIEW)),
):
    return await services.list_definitions(db)


@router.post("", response_model=WorkflowDefinitionOut, status_code=201)
async def create_workflow(
    body: WorkflowDefinitionCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(*WORKFLOWS_CREATE)),
):
    return await services.create_definition(db, current_user, body)


@router.get("/{definition_id}", response_model=WorkflowDefinitionOut)
async def get_workflow(
    definition_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(*WORKFLOWS_VIEW)),
):
    return await services.get_definition(db, definition_id)


@router.patch("/{definition_id}", response_model=WorkflowDefinitionOut)
async def update_workflow(
    definition_id: uuid.UUID,
    body: WorkflowDefinitionUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(*WORKFLOWS_UPDATE)),
):
    return await services.update_definition(db, definition_id, body)


@router.delete("/{definition_id}", status_code=204)
async def delete_workflow(
    definition_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(*WORKFLOWS_DELETE)),
):
    await services.delete_definition(db, definition_id)


@router.post("/import", response_model=ImportBpmnResult)
async def import_bpmn(
    body: ImportBpmnRequest,
    current_user: User = Depends(require_permission(*WORKFLOWS_CREATE)),
):
    """Parses the uploaded XML just far enough to list the process ids it
    contains, so an admin can pick which one to register as a version —
    nothing is persisted here."""
    return ImportBpmnResult(process_ids=list_process_ids(body.bpmn_xml))


# ── Versions ─────────────────────────────────────────────────────────────

@router.get("/{definition_id}/versions", response_model=List[WorkflowVersionListOut])
async def list_versions(
    definition_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(*WORKFLOWS_VIEW)),
):
    return await services.list_versions(db, definition_id)


@router.post("/{definition_id}/versions", response_model=WorkflowVersionOut, status_code=201)
async def create_version(
    definition_id: uuid.UUID,
    body: WorkflowVersionCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(*WORKFLOWS_UPDATE)),
):
    return await services.create_version(db, current_user, definition_id, body)


@router.get("/{definition_id}/versions/{version_id}", response_model=WorkflowVersionOut)
async def get_version(
    definition_id: uuid.UUID,
    version_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(*WORKFLOWS_VIEW)),
):
    return await services.get_version(db, definition_id, version_id)


@router.put("/{definition_id}/versions/{version_id}", response_model=WorkflowVersionOut)
async def update_version(
    definition_id: uuid.UUID,
    version_id: uuid.UUID,
    body: WorkflowVersionCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(*WORKFLOWS_UPDATE)),
):
    return await services.update_version(db, definition_id, version_id, body)


@router.get("/{definition_id}/versions/{version_id}/export")
async def export_version(
    definition_id: uuid.UUID,
    version_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(*WORKFLOWS_VIEW)),
):
    version = await services.get_version(db, definition_id, version_id)
    return Response(content=version.bpmn_xml, media_type="application/xml")


@router.post("/{definition_id}/versions/{version_id}/validate", response_model=ValidationResultOut)
async def validate_version(
    definition_id: uuid.UUID,
    version_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(*WORKFLOWS_VIEW)),
):
    return _validation_out(await services.validate_version(db, definition_id, version_id))


@router.post("/{definition_id}/versions/{version_id}/publish", response_model=WorkflowVersionOut)
async def publish_version(
    definition_id: uuid.UUID,
    version_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(*WORKFLOWS_PUBLISH)),
):
    return await services.publish_version(db, current_user, definition_id, version_id)


@router.post("/{definition_id}/versions/{version_id}/archive", response_model=WorkflowVersionOut)
async def archive_version(
    definition_id: uuid.UUID,
    version_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(*WORKFLOWS_ARCHIVE)),
):
    return await services.archive_version(db, definition_id, version_id)


# ── Instances (read + cancel only — see module docstring) ────────────────

@instances_router.get("", response_model=List[WorkflowInstanceOut])
async def list_workflow_instances(
    definition_id: Optional[uuid.UUID] = Query(None),
    status: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(*WORKFLOW_INSTANCES_VIEW)),
):
    return await repositories.list_instances(db, definition_id=definition_id, status=status)


@instances_router.get("/by-object/{business_object_type}/{business_object_id}", response_model=Optional[WorkflowInstanceOut])
async def get_instance_by_object(
    business_object_type: str,
    business_object_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(*WORKFLOW_INSTANCES_VIEW)),
):
    instance = await repositories.get_instance_for_object(db, business_object_type, business_object_id)
    if instance is None:
        return None
    return await services.get_instance_scoped(db, instance.id)


@instances_router.get("/{instance_id}", response_model=WorkflowInstanceOut)
async def get_workflow_instance(
    instance_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(*WORKFLOW_INSTANCES_VIEW)),
):
    return await services.get_instance_scoped(db, instance_id)


@instances_router.post("/{instance_id}/cancel", response_model=WorkflowInstanceOut)
async def cancel_workflow_instance(
    instance_id: uuid.UUID,
    body: TaskActionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(*WORKFLOW_INSTANCES_CANCEL)),
):
    from app.workflow import executor
    instance = await services.get_instance_scoped(db, instance_id)
    instance = await executor.cancel_instance(db, current_user, instance, comment=body.comment)
    await hooks.fire_if_terminal(db, instance)
    return instance


@instances_router.get("/{instance_id}/history", response_model=List[WorkflowHistoryOut])
async def get_instance_history(
    instance_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(*WORKFLOW_INSTANCES_VIEW)),
):
    await services.get_instance_scoped(db, instance_id)
    return await repositories.list_history_for_instance(db, instance_id)


@instances_router.get("/{instance_id}/variables", response_model=List[WorkflowVariableOut])
async def get_instance_variables(
    instance_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(*WORKFLOW_INSTANCES_VIEW)),
):
    await services.get_instance_scoped(db, instance_id)
    return await repositories.list_variables_for_instance(db, instance_id)
