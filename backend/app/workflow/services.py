"""WorkflowService — definition/version CRUD, validate, publish, archive
(the design-time half). Execution — starting/advancing/cancelling
instances — lives in executor.py. Ported from PoultryPro-CBF's
app/workflow/services.py with branch scoping stripped, and the is_system
bypass dropped: this hub has no hidden bypass role, so a built-in
definition that already has a published version is simply immutable via
the API for everyone (the bootstrap carve-out — a plain admin may still
build and publish a built-in definition's *first* version — is preserved,
same as migration 012's own seed relies on)."""
import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.workflow import repositories
from app.workflow.models import WorkflowDefinition, WorkflowInstance, WorkflowVariable, WorkflowVersion, WorkflowVersionStatus
from app.workflow.parser import ValidationResult, validate_bpmn
from app.workflow.schemas import WorkflowDefinitionCreate, WorkflowDefinitionUpdate, WorkflowVersionCreate

_LOCKED = "This is a built-in workflow — its structure can't be changed from the UI."


async def list_definitions(db: AsyncSession) -> List[WorkflowDefinition]:
    return await repositories.list_definitions(db)


async def get_definition(db: AsyncSession, definition_id: uuid.UUID) -> WorkflowDefinition:
    result = await db.execute(select(WorkflowDefinition).where(WorkflowDefinition.id == definition_id))
    definition = result.scalar_one_or_none()
    if not definition:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return definition


async def create_definition(db: AsyncSession, current_user: User, body: WorkflowDefinitionCreate) -> WorkflowDefinition:
    existing = (await db.execute(select(WorkflowDefinition.id).where(WorkflowDefinition.key == body.key))).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail=f"A workflow with key '{body.key}' already exists.")

    definition = WorkflowDefinition(
        key=body.key, name=body.name, description=body.description,
        is_system=body.is_system, created_by=current_user.id,
    )
    db.add(definition)
    await db.flush()
    await db.refresh(definition)
    return definition


async def update_definition(db: AsyncSession, definition_id: uuid.UUID, body: WorkflowDefinitionUpdate) -> WorkflowDefinition:
    definition = await get_definition(db, definition_id)
    if definition.is_system and (body.name is not None or body.description is not None):
        raise HTTPException(status_code=403, detail=_LOCKED)
    if body.name is not None:
        definition.name = body.name
    if body.description is not None:
        definition.description = body.description
    if body.is_active is not None:
        definition.is_active = body.is_active
    await db.flush()
    await db.refresh(definition)
    return definition


async def delete_definition(db: AsyncSession, definition_id: uuid.UUID) -> None:
    """Deleting cascades to versions, but not to instances (no ondelete on
    workflow_instances.definition_id — instances are permanent history). A
    definition with any instance ever run against it can't be deleted; the
    FK's default RESTRICT catches this."""
    definition = await get_definition(db, definition_id)
    if definition.is_system:
        if await repositories.get_published_version(db, definition_id) is not None:
            raise HTTPException(status_code=403, detail="Cannot delete a built-in workflow.")
    try:
        await db.delete(definition)
        await db.flush()
    except IntegrityError:
        raise HTTPException(status_code=409, detail="Cannot delete a workflow that has running or historical instances.")


async def _get_version(db: AsyncSession, definition_id: uuid.UUID, version_id: uuid.UUID) -> WorkflowVersion:
    await get_definition(db, definition_id)
    result = await db.execute(
        select(WorkflowVersion).where(WorkflowVersion.id == version_id, WorkflowVersion.definition_id == definition_id)
    )
    version = result.scalar_one_or_none()
    if not version:
        raise HTTPException(status_code=404, detail="Version not found")
    return version


async def _assert_definition_mutable(db: AsyncSession, definition_id: uuid.UUID) -> WorkflowDefinition:
    """Built-in workflows are frozen: no new/edited/published/archived
    versions once a version has been published. Bootstrap carve-out: a
    built-in flow with no published version yet is not a functioning flow —
    a plain admin may build and publish its *first* version (how migration
    012 provisions the seeded deployment-action approval flow). The
    is_active toggle stays editable regardless."""
    definition = await get_definition(db, definition_id)
    if definition.is_system:
        if await repositories.get_published_version(db, definition_id) is not None:
            raise HTTPException(status_code=403, detail=_LOCKED)
    return definition


async def list_versions(db: AsyncSession, definition_id: uuid.UUID) -> List[WorkflowVersion]:
    await get_definition(db, definition_id)
    return await repositories.list_versions(db, definition_id)


async def get_version(db: AsyncSession, definition_id: uuid.UUID, version_id: uuid.UUID) -> WorkflowVersion:
    return await _get_version(db, definition_id, version_id)


async def create_version(db: AsyncSession, current_user: User, definition_id: uuid.UUID, body: WorkflowVersionCreate) -> WorkflowVersion:
    definition = await _assert_definition_mutable(db, definition_id)
    result = await db.execute(select(func.max(WorkflowVersion.version)).where(WorkflowVersion.definition_id == definition_id))
    next_version = (result.scalar() or 0) + 1
    version = WorkflowVersion(
        definition_id=definition.id, version=next_version, bpmn_xml=body.bpmn_xml,
        process_id=body.process_id, notes=body.notes, created_by=current_user.id,
    )
    db.add(version)
    await db.flush()
    await db.refresh(version)
    return version


async def update_version(db: AsyncSession, definition_id: uuid.UUID, version_id: uuid.UUID, body: WorkflowVersionCreate) -> WorkflowVersion:
    await _assert_definition_mutable(db, definition_id)
    version = await _get_version(db, definition_id, version_id)
    if version.status != WorkflowVersionStatus.draft:
        raise HTTPException(status_code=400, detail="Only draft versions can be edited — publish a new version instead.")
    version.bpmn_xml = body.bpmn_xml
    version.process_id = body.process_id
    version.notes = body.notes
    await db.flush()
    await db.refresh(version)
    return version


async def validate_version(db: AsyncSession, definition_id: uuid.UUID, version_id: uuid.UUID) -> ValidationResult:
    version = await _get_version(db, definition_id, version_id)
    return validate_bpmn(version.bpmn_xml, version.process_id)


async def publish_version(db: AsyncSession, current_user: User, definition_id: uuid.UUID, version_id: uuid.UUID) -> WorkflowVersion:
    await _assert_definition_mutable(db, definition_id)
    version = await _get_version(db, definition_id, version_id)
    result = validate_bpmn(version.bpmn_xml, version.process_id)
    if not result.is_valid:
        raise HTTPException(status_code=400, detail="; ".join(result.errors))

    # The partial unique index (definition_id) WHERE status='published'
    # allows only one published version per definition — archive whichever
    # one currently holds that slot before taking it. Two flushes makes the
    # ordering explicit (see PoultryPro-CBF's same comment for why).
    existing = await repositories.get_published_version(db, definition_id)
    if existing is not None and existing.id != version.id:
        existing.status = WorkflowVersionStatus.archived
        await db.flush()
        repositories.invalidate_spec_cache(existing.id)

    version.status = WorkflowVersionStatus.published
    version.published_at = datetime.utcnow()
    version.published_by = current_user.id
    await db.flush()
    repositories.invalidate_spec_cache(version.id)
    await db.refresh(version)
    return version


async def archive_version(db: AsyncSession, definition_id: uuid.UUID, version_id: uuid.UUID) -> WorkflowVersion:
    await _assert_definition_mutable(db, definition_id)
    version = await _get_version(db, definition_id, version_id)
    version.status = WorkflowVersionStatus.archived
    await db.flush()
    repositories.invalidate_spec_cache(version.id)
    await db.refresh(version)
    return version


async def resolve_definition_by_key(db: AsyncSession, key: str) -> WorkflowDefinition:
    result = await db.execute(
        select(WorkflowDefinition).where(WorkflowDefinition.key == key, WorkflowDefinition.is_active.is_(True))
    )
    definition = result.scalar_one_or_none()
    if not definition:
        raise HTTPException(status_code=404, detail=f"No active workflow definition found for key '{key}'.")
    return definition


async def get_instance_scoped(db: AsyncSession, instance_id: uuid.UUID) -> WorkflowInstance:
    result = await db.execute(select(WorkflowInstance).where(WorkflowInstance.id == instance_id))
    instance = result.scalar_one_or_none()
    if not instance:
        raise HTTPException(status_code=404, detail="Workflow instance not found")
    return instance


async def set_variable(db: AsyncSession, instance: WorkflowInstance, current_user: User, name: str, value) -> None:
    result = await db.execute(
        select(WorkflowVariable).where(WorkflowVariable.instance_id == instance.id, WorkflowVariable.name == name)
    )
    existing = result.scalar_one_or_none()
    if existing:
        existing.value = value
        existing.value_type = type(value).__name__
        existing.updated_at = datetime.utcnow()
        existing.updated_by = current_user.id
    else:
        db.add(WorkflowVariable(
            instance_id=instance.id, name=name, value=value, value_type=type(value).__name__,
            is_input=False, updated_by=current_user.id,
        ))
    await db.flush()
