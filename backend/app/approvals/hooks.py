"""Registry letting a module (deployments, ...) react once a workflow
instance touching one of its business objects reaches a terminal status,
without approvals/workflow ever importing that module — the module
registers a callback for its own `business_object_type` instead, keeping
the dependency pointed one way. Ported verbatim from PoultryPro-CBF's
app/approvals/hooks.py. app/approvals/deployment_hooks.py is the reference
implementation this exists for: it defers the real renew/suspend/
change-plan call to the client deployment until its hook fires with
status=completed.

Also holds pre-approve transforms: a business module can register a
callback for one of its own (business_object_type, step_key) pairs to
derive extra variables from an approver's raw submitted `data` before it's
persisted and the task is completed. Same one-directional import shape as
completion hooks.
"""
from typing import Awaitable, Callable, Dict, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from app.workflow.models import InstanceStatus, WorkflowInstance

CompletionHook = Callable[[AsyncSession, WorkflowInstance], Awaitable[None]]
PreApproveTransform = Callable[[AsyncSession, WorkflowInstance, dict], Awaitable[dict]]

_TERMINAL = {InstanceStatus.completed, InstanceStatus.rejected, InstanceStatus.cancelled, InstanceStatus.error}

_hooks: Dict[str, CompletionHook] = {}
_pre_approve_transforms: Dict[Tuple[str, str], PreApproveTransform] = {}


def register_completion_hook(business_object_type: str, hook: CompletionHook) -> None:
    _hooks[business_object_type] = hook


async def fire_if_terminal(db: AsyncSession, instance: WorkflowInstance) -> None:
    if instance.status not in _TERMINAL:
        return
    hook = _hooks.get(instance.business_object_type)
    if hook:
        await hook(db, instance)


def register_pre_approve_transform(business_object_type: str, step_key: str, transform: PreApproveTransform) -> None:
    _pre_approve_transforms[(business_object_type, step_key)] = transform


async def apply_pre_approve_transform(db: AsyncSession, instance: WorkflowInstance, step_key: str, data: dict) -> dict:
    transform = _pre_approve_transforms.get((instance.business_object_type, step_key))
    if transform is None:
        return data
    return await transform(db, instance, data)
