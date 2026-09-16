"""ApprovalService — the permission-aware layer between the approvals API
and workflow/executor.py. Two checks gate every action on a task,
deliberately kept separate:
  1. Casbin (authorization) — approvals.act is checked by the router
     dependency before reaching here; if the BPMN task additionally
     declares casbin_resource/casbin_action, _assert_can_act checks that
     specific permission too.
  2. Data (routing) — is this user actually a candidate/assignee for
     *this* task. A pure data check, nothing to do with Casbin.
     approvals.act_any bypasses this for an admin override.
Not-a-candidate comes back as 404, matching the rest of the app's
convention (deployments.py's _get_visible_or_404) of hiding rather than
exposing rows a user has no claim to.

Ported from PoultryPro-CBF's app/approvals/services.py with branch scoping
stripped.
"""
import uuid
from typing import List, Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.approvals import hooks
from app.core import event_types as et
from app.core.permissions import has_permission
from app.models import User
from app.services.events import record_event
from app.workflow import executor, repositories
from app.workflow.models import WorkflowInstance, WorkflowTask


async def list_my_tasks(db: AsyncSession, current_user: User, *, status: Optional[str] = None) -> List[WorkflowTask]:
    return await repositories.list_my_tasks(db, current_user, status=status)


async def list_tasks(db: AsyncSession, *, status: Optional[str] = None) -> List[WorkflowTask]:
    """Every task, not just the caller's own — for an admin reviewing the
    approval queue as a whole."""
    q = select(WorkflowTask).order_by(WorkflowTask.created_at.desc())
    if status is not None:
        q = q.where(WorkflowTask.status == status)
    result = await db.execute(q)
    return list(result.scalars().all())


async def get_task(db: AsyncSession, task_id: uuid.UUID) -> WorkflowTask:
    task = await repositories.get_task(db, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


async def _assert_can_act(db: AsyncSession, current_user: User, task: WorkflowTask, *, act_any: bool) -> None:
    if task.casbin_resource and task.casbin_action:
        if not await has_permission(str(current_user.id), task.casbin_resource, task.casbin_action):
            raise HTTPException(status_code=403, detail=f"Missing permission: {task.casbin_resource}.{task.casbin_action}")

    if act_any:
        return

    candidate_ids = {str(u) for u in task.candidate_user_ids}
    is_assigned = task.assigned_user_id is not None and task.assigned_user_id == current_user.id
    is_candidate = str(current_user.id) in candidate_ids
    if not (is_assigned or is_candidate):
        raise HTTPException(status_code=404, detail="Task not found")


async def approve(db: AsyncSession, current_user: User, task_id: uuid.UUID, *, comment: Optional[str], data: dict, act_any: bool) -> WorkflowInstance:
    task = await get_task(db, task_id)
    await _assert_can_act(db, current_user, task, act_any=act_any)
    instance_row = (await db.execute(
        select(WorkflowInstance).where(WorkflowInstance.id == task.instance_id)
    )).scalar_one()
    data = await hooks.apply_pre_approve_transform(db, instance_row, task.step_key, data)
    instance = await executor.approve_task(db, current_user, task, comment=comment, data=data)
    await hooks.fire_if_terminal(db, instance, actor=current_user)
    return instance


async def reject(db: AsyncSession, current_user: User, task_id: uuid.UUID, *, comment: Optional[str], act_any: bool) -> WorkflowInstance:
    task = await get_task(db, task_id)
    await _assert_can_act(db, current_user, task, act_any=act_any)
    instance = await executor.reject_task(db, current_user, task, comment=comment)
    await hooks.fire_if_terminal(db, instance, actor=current_user)
    return instance


async def reassign(db: AsyncSession, current_user: User, task_id: uuid.UUID, *, new_user_id: uuid.UUID, comment: Optional[str]) -> WorkflowTask:
    task = await get_task(db, task_id)
    instance = await db.get(WorkflowInstance, task.instance_id)
    reassigned = await executor.reassign_task(db, current_user, task, new_user_id=new_user_id, comment=comment)
    record_event(
        db, event_type=et.APPROVAL_REASSIGNED, source=et.SOURCE_HUB, actor_type=et.ACTOR_STAFF, actor_id=current_user.id,
        entity_type=et.ENTITY_WORKFLOW_INSTANCE, entity_id=task.instance_id,
        correlation_id=(instance.correlation_id if instance else None) or task.instance_id,
        metadata={"task_id": str(task_id), "new_user_id": str(new_user_id)},
    )
    return reassigned
