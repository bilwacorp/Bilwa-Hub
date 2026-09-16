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
from typing import Awaitable, Callable, Dict, Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import event_types as et
from app.models import OperationalEventStatus, User
from app.services.events import record_event
from app.workflow.models import InstanceStatus, WorkflowInstance

CompletionHook = Callable[[AsyncSession, WorkflowInstance], Awaitable[None]]
PreApproveTransform = Callable[[AsyncSession, WorkflowInstance, dict], Awaitable[dict]]

_TERMINAL = {InstanceStatus.completed, InstanceStatus.rejected, InstanceStatus.cancelled, InstanceStatus.error}

# Every terminal InstanceStatus this generic engine can reach, mapped to
# the one OperationalEvent it emits (event_type, status) — see
# fire_if_terminal's docstring for why this stays generic (no assumption
# about business_object_type) while deployment_hooks.py's registered
# completion hooks emit the domain-specific event alongside it. `status`
# here means "did the engine do what it was asked" — completed/rejected/
# cancelled are all outcomes the engine reached correctly (a rejection is
# a valid business decision, not a system fault); only `error` (no
# approver rule resolved) is an actual failure.
_TERMINAL_EVENT: Dict[InstanceStatus, tuple] = {
    InstanceStatus.completed: (et.APPROVAL_APPROVED, OperationalEventStatus.success),
    InstanceStatus.rejected: (et.APPROVAL_REJECTED, OperationalEventStatus.success),
    InstanceStatus.cancelled: (et.WORKFLOW_CANCELLED, OperationalEventStatus.success),
    InstanceStatus.error: (et.WORKFLOW_ERRORED, OperationalEventStatus.failure),
}

_hooks: Dict[str, CompletionHook] = {}
_pre_approve_transforms: Dict[Tuple[str, str], PreApproveTransform] = {}


def register_completion_hook(business_object_type: str, hook: CompletionHook) -> None:
    _hooks[business_object_type] = hook


async def fire_if_terminal(db: AsyncSession, instance: WorkflowInstance, *, actor: Optional[User] = None) -> None:
    """The single choke point every terminal transition of this generic
    engine passes through — called from approvals/integration.py (the
    synchronous-auto-complete-on-start edge case), approvals/services.py's
    approve()/reject(), and workflow/api.py's cancel endpoint. Emits one
    generic OperationalEvent here (entity_type=workflow_instance — this
    code has no business knowing whether business_object_id is a
    deployment id or something else) before firing the domain-specific
    completion hook, which — for deployment actions — emits its own
    deployment.<verb>_executed/_failed event once it knows the real
    outcome (see deployment_hooks.py)."""
    if instance.status not in _TERMINAL:
        return
    event_type, event_status = _TERMINAL_EVENT[instance.status]
    record_event(
        db, event_type=event_type, source=et.SOURCE_ENGINE,
        actor_type=et.ACTOR_STAFF if actor else et.ACTOR_SYSTEM, actor_id=actor.id if actor else None,
        entity_type=et.ENTITY_WORKFLOW_INSTANCE, entity_id=instance.id,
        correlation_id=instance.correlation_id or instance.id, status=event_status,
        metadata={"business_object_type": instance.business_object_type, "business_object_id": str(instance.business_object_id)},
    )
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
