"""Data access for the workflow package, plus the parsed-spec cache the
executor uses to avoid re-parsing BPMN XML on every step. Ported from
PoultryPro-CBF's app/workflow/repositories.py with branch scoping
stripped — every definition/instance here is visible to anyone holding the
relevant view permission, same as every other catalog table in this hub.
"""
import collections
import uuid
from typing import Dict, List, Optional, Tuple

import anyio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.workflow import engine, parser
from app.workflow.models import (
    WorkflowDefinition, WorkflowHistory, WorkflowInstance, WorkflowTask, WorkflowVariable, WorkflowVersion,
    WorkflowVersionStatus,
)

# ── Parsed-spec cache ───────────────────────────────────────────────────
# Keyed by (immutable) workflow_version_id. Caches plain dicts, never live
# spec objects — see engine.py's module docstring for why sharing a live
# BpmnProcessSpec across concurrent instances is a data race, not a
# hypothetical one. A bounded LRU since a long-running process could
# otherwise accumulate one entry per published version forever.
_SPEC_CACHE_MAXSIZE = 128
_spec_cache: "collections.OrderedDict[uuid.UUID, Tuple[dict, Dict[str, dict]]]" = collections.OrderedDict()


async def get_cached_spec_dicts(version: WorkflowVersion) -> Tuple[dict, Dict[str, dict]]:
    cached = _spec_cache.get(version.id)
    if cached is not None:
        _spec_cache.move_to_end(version.id)
        return cached

    # Parsing is CPU-bound lxml work — off the event loop.
    parsed = await anyio.to_thread.run_sync(parser.parse_workflow, version.bpmn_xml, version.process_id)
    spec_dict = engine.spec_to_cache_dict(parsed.spec)
    subprocess_dicts = {name: engine.spec_to_cache_dict(s) for name, s in parsed.subprocess_specs.items()}

    _spec_cache[version.id] = (spec_dict, subprocess_dicts)
    _spec_cache.move_to_end(version.id)
    if len(_spec_cache) > _SPEC_CACHE_MAXSIZE:
        _spec_cache.popitem(last=False)
    return spec_dict, subprocess_dicts


def invalidate_spec_cache(version_id: uuid.UUID) -> None:
    _spec_cache.pop(version_id, None)


# ── Definitions ──────────────────────────────────────────────────────────

async def list_definitions(db: AsyncSession) -> List[WorkflowDefinition]:
    result = await db.execute(select(WorkflowDefinition).order_by(WorkflowDefinition.created_at.desc()))
    return list(result.scalars().all())


async def list_versions(db: AsyncSession, definition_id: uuid.UUID) -> List[WorkflowVersion]:
    result = await db.execute(
        select(WorkflowVersion).where(WorkflowVersion.definition_id == definition_id).order_by(WorkflowVersion.version.desc())
    )
    return list(result.scalars().all())


async def get_published_version(db: AsyncSession, definition_id: uuid.UUID) -> Optional[WorkflowVersion]:
    result = await db.execute(
        select(WorkflowVersion).where(
            WorkflowVersion.definition_id == definition_id,
            WorkflowVersion.status == WorkflowVersionStatus.published,
        )
    )
    return result.scalar_one_or_none()


# ── Instances / tasks / history / variables ─────────────────────────────

async def list_instances(
    db: AsyncSession, *, definition_id: Optional[uuid.UUID] = None, status: Optional[str] = None,
) -> List[WorkflowInstance]:
    q = select(WorkflowInstance).order_by(WorkflowInstance.started_at.desc())
    if definition_id is not None:
        q = q.where(WorkflowInstance.definition_id == definition_id)
    if status is not None:
        q = q.where(WorkflowInstance.status == status)
    result = await db.execute(q)
    return list(result.scalars().all())


async def get_instance_for_object(db: AsyncSession, business_object_type: str, business_object_id: uuid.UUID) -> Optional[WorkflowInstance]:
    result = await db.execute(
        select(WorkflowInstance)
        .where(
            WorkflowInstance.business_object_type == business_object_type,
            WorkflowInstance.business_object_id == business_object_id,
        )
        .order_by(WorkflowInstance.started_at.desc())
    )
    return result.scalars().first()


async def list_tasks_for_instance(db: AsyncSession, instance_id: uuid.UUID) -> List[WorkflowTask]:
    result = await db.execute(select(WorkflowTask).where(WorkflowTask.instance_id == instance_id).order_by(WorkflowTask.created_at))
    return list(result.scalars().all())


async def list_history_for_instance(db: AsyncSession, instance_id: uuid.UUID) -> List[WorkflowHistory]:
    result = await db.execute(select(WorkflowHistory).where(WorkflowHistory.instance_id == instance_id).order_by(WorkflowHistory.created_at))
    return list(result.scalars().all())


async def list_variables_for_instance(db: AsyncSession, instance_id: uuid.UUID) -> List[WorkflowVariable]:
    result = await db.execute(select(WorkflowVariable).where(WorkflowVariable.instance_id == instance_id).order_by(WorkflowVariable.name))
    return list(result.scalars().all())


async def list_my_tasks(db: AsyncSession, current_user, *, status: Optional[str] = None) -> List[WorkflowTask]:
    """Tasks assigned to, or candidate for, the current user — the
    approvals inbox query."""
    from sqlalchemy import or_
    q = select(WorkflowTask).where(
        or_(
            WorkflowTask.assigned_user_id == current_user.id,
            WorkflowTask.candidate_user_ids.contains([str(current_user.id)]),
        )
    ).order_by(WorkflowTask.created_at)
    if status is not None:
        q = q.where(WorkflowTask.status == status)
    result = await db.execute(q)
    return list(result.scalars().all())


async def get_task(db: AsyncSession, task_id: uuid.UUID) -> Optional[WorkflowTask]:
    result = await db.execute(select(WorkflowTask).where(WorkflowTask.id == task_id))
    return result.scalar_one_or_none()
