"""Approval inbox + task actions. Workflow/rule CRUD live in
app/workflow/api.py and app/rules/api.py — this router is deliberately
narrow: it's the surface a day-to-day approver actually touches. Ported
from PoultryPro-CBF's app/approvals/api.py with branch context stripped."""
import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.core.permissions import (
    require_permission, has_permission,
    APPROVALS_VIEW, APPROVALS_ACT, APPROVALS_ACT_ANY, APPROVALS_REASSIGN,
)
from app.models import User
from app.approvals import services
from app.workflow.schemas import TaskActionRequest, TaskReassignRequest, WorkflowInstanceOut, WorkflowTaskOut

router = APIRouter(prefix="/approvals", tags=["approvals"])


@router.get("/my-tasks", response_model=List[WorkflowTaskOut])
async def list_my_tasks(
    status: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(*APPROVALS_VIEW)),
):
    return await services.list_my_tasks(db, current_user, status=status)


@router.get("/tasks", response_model=List[WorkflowTaskOut])
async def list_tasks(
    status: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(*APPROVALS_VIEW)),
):
    return await services.list_tasks(db, status=status)


@router.get("/tasks/{task_id}", response_model=WorkflowTaskOut)
async def get_task(
    task_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(*APPROVALS_VIEW)),
):
    return await services.get_task(db, task_id)


@router.post("/tasks/{task_id}/approve", response_model=WorkflowInstanceOut)
async def approve_task(
    task_id: uuid.UUID,
    body: TaskActionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(*APPROVALS_ACT)),
):
    act_any = await has_permission(str(current_user.id), *APPROVALS_ACT_ANY)
    return await services.approve(db, current_user, task_id, comment=body.comment, data=body.data, act_any=act_any)


@router.post("/tasks/{task_id}/reject", response_model=WorkflowInstanceOut)
async def reject_task(
    task_id: uuid.UUID,
    body: TaskActionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(*APPROVALS_ACT)),
):
    act_any = await has_permission(str(current_user.id), *APPROVALS_ACT_ANY)
    return await services.reject(db, current_user, task_id, comment=body.comment, act_any=act_any)


@router.post("/tasks/{task_id}/reassign", response_model=WorkflowTaskOut)
async def reassign_task(
    task_id: uuid.UUID,
    body: TaskReassignRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(*APPROVALS_REASSIGN)),
):
    return await services.reassign(db, current_user, task_id, new_user_id=body.user_id, comment=body.comment)
