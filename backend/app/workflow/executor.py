"""The workflow advance loop — the only place WorkflowInstance.serialized_state
is read, mutated, and written back. A live BpmnWorkflow object is never held
across HTTP requests: every entry point here loads the instance row FOR
UPDATE, deserializes fresh from JSONB, does its work, re-serializes, and
flushes — so two concurrent approvals on the same instance serialize
through the row lock rather than racing each other's in-memory state.

Rule resolution never hardcodes an approver: every human task's candidates
come from rules/services.find_first_matching_rule + rules/resolvers.resolve.
A step with no matching rule, or one whose rule resolves to zero approvers,
moves the instance to `error` rather than silently stalling.

Ported from PoultryPro-CBF's app/workflow/executor.py with branch scoping
stripped, push notifications dropped (this hub is email/WhatsApp-only, no
mobile app), and the shared create_with_code_retry helper replaced with a
plain random instance_code (a UNIQUE-constraint collision at 4 random
bytes is astronomically unlikely at this hub's scale, so no retry loop is
worth the complexity of porting that helper).
"""
import secrets
import uuid
from datetime import datetime
from typing import Any, Dict, Optional, Set

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.models import User
from app.rules import resolvers
from app.rules import services as rule_services
from app.rules.models import RuleActionType
from app.services.notifications.service import NotificationService
from app.workflow import engine, repositories
from app.workflow.models import (
    HistoryEventType, InstanceStatus, WorkflowHistory, WorkflowInstance, WorkflowTask,
    WorkflowTaskStatus, WorkflowVariable, WorkflowVersion,
)

_MAX_ADVANCE_ITERATIONS = 50


async def _load_instance_locked(db: AsyncSession, instance_id: uuid.UUID) -> WorkflowInstance:
    result = await db.execute(
        select(WorkflowInstance)
        .options(selectinload(WorkflowInstance.definition), selectinload(WorkflowInstance.version))
        .where(WorkflowInstance.id == instance_id)
        .with_for_update()
    )
    instance = result.scalar_one_or_none()
    if not instance:
        raise HTTPException(status_code=404, detail="Workflow instance not found")
    return instance


async def _record_history(
    db: AsyncSession, instance: WorkflowInstance, event_type: HistoryEventType, *,
    task_id: Optional[uuid.UUID] = None, actor: Optional[User] = None,
    from_status: Optional[str] = None, to_status: Optional[str] = None,
    comment: Optional[str] = None, detail: Optional[dict] = None,
) -> None:
    db.add(WorkflowHistory(
        instance_id=instance.id, task_id=task_id, event_type=event_type,
        actor_user_id=actor.id if actor else None, actor_username=actor.username if actor else None,
        from_status=from_status, to_status=to_status, comment=comment, detail=detail,
    ))


async def _notify_candidate_approvers(
    db: AsyncSession, instance: WorkflowInstance, wf_task: WorkflowTask, approver_ids: Set[uuid.UUID],
) -> None:
    """Notify everyone a newly-created human task was assigned to, by
    email (if they have one on file) — this hub has no mobile app/push
    infrastructure, unlike PoultryPro-CBF. Never blocks the workflow from
    advancing. instance.definition is eagerly loaded by
    _load_instance_locked (every entry point routes through it)."""
    approvers = (await db.execute(select(User).where(User.id.in_(approver_ids)))).scalars().all()
    if not approvers:
        return

    notifications = NotificationService(db)
    request = f"{instance.definition.name} ({instance.instance_code})"
    action_url = f"{settings.FRONTEND_URL.rstrip('/')}/approvals"
    for approver in approvers:
        if approver.email:
            await notifications.send_approval(
                recipient=approver.email,
                approver=approver.full_name or approver.username,
                request=request,
                action_url=action_url,
            )


async def _seed_variables(db: AsyncSession, instance: WorkflowInstance, variables: Dict[str, Any], *, actor: Optional[User]) -> None:
    for name, value in variables.items():
        db.add(WorkflowVariable(
            instance_id=instance.id, name=name, value=value, value_type=type(value).__name__,
            is_input=True, updated_by=actor.id if actor else None,
        ))


async def _upsert_variable(db: AsyncSession, instance: WorkflowInstance, name: Optional[str], value: Any) -> None:
    if not name:
        return
    result = await db.execute(
        select(WorkflowVariable).where(WorkflowVariable.instance_id == instance.id, WorkflowVariable.name == name)
    )
    existing = result.scalar_one_or_none()
    if existing:
        existing.value = value
        existing.value_type = type(value).__name__
        existing.updated_at = datetime.utcnow()
    else:
        db.add(WorkflowVariable(instance_id=instance.id, name=name, value=value, value_type=type(value).__name__, is_input=False))
    await db.flush()


async def _load_variables(db: AsyncSession, instance_id: uuid.UUID) -> Dict[str, Any]:
    rows = await repositories.list_variables_for_instance(db, instance_id)
    return {r.name: r.value for r in rows}


async def _build_rule_context(db: AsyncSession, instance: WorkflowInstance, step_key: Optional[str]) -> Dict[str, Any]:
    variables = await _load_variables(db, instance.id)
    return {
        "business_object_type": instance.business_object_type,
        "business_object_id": str(instance.business_object_id),
        "started_by": str(instance.started_by),
        "step_key": step_key,
        **variables,
    }


async def _apply_rule_actions(db: AsyncSession, instance: WorkflowInstance, rule, context: Dict[str, Any]):
    """Apply every action on a matched rule. `set_variable` actions are pure
    side effects; the routing outcome is whichever of
    assign_approver/auto_approve/skip_step appears (a rule is expected to
    have exactly one routing action — if it names more than one, the last
    wins)."""
    if rule is None:
        raise RuntimeError("No approval rule matched this step — add a rule to route it.")

    outcome = None
    for action in sorted(rule.actions, key=lambda a: a.sequence):
        if action.action_type == RuleActionType.set_variable:
            await _upsert_variable(db, instance, action.variable_name, action.variable_value)
        elif action.action_type == RuleActionType.auto_approve:
            outcome = "auto_approve"
        elif action.action_type == RuleActionType.skip_step:
            outcome = "skip"
        elif action.action_type == RuleActionType.assign_approver:
            outcome = await resolvers.resolve(
                db, action,
                business_object_type=instance.business_object_type, business_object_id=instance.business_object_id,
            )
    if outcome is None:
        raise RuntimeError(f"Rule '{rule.key}' has no routing action (assign_approver / auto_approve / skip_step).")
    return outcome


async def advance(db: AsyncSession, instance: WorkflowInstance) -> WorkflowInstance:
    """Run the workflow forward from its current serialized state until it
    blocks on a human task (creating WorkflowTask rows for any newly-ready
    ones) or finishes. Caller must already hold the instance row lock —
    every public entry point below goes through _load_instance_locked."""
    if instance.status != InstanceStatus.running:
        return instance

    try:
        spec_dict, subprocess_dicts = await repositories.get_cached_spec_dicts(instance.version)
        wf = engine.deserialize(instance.serialized_state, spec_dict, subprocess_dicts)
        existing_task_ids = {t.spiff_task_id for t in await repositories.list_tasks_for_instance(db, instance.id)}

        for _ in range(_MAX_ADVANCE_ITERATIONS):
            engine.run_engine_steps(wf)
            ready = engine.ready_human_tasks(wf)
            new_ready = [t for t in ready if engine.task_id_str(t) not in existing_task_ids]
            if not new_ready:
                break

            progressed = False
            for task in new_ready:
                props = engine.task_extension_properties(task)
                step_key = props.get("stepKey")
                context = await _build_rule_context(db, instance, step_key)
                rule, error = await rule_services.find_first_matching_rule(
                    db, definition_id=instance.definition_id, step_key=step_key, context=context,
                )
                if error:
                    await _record_history(db, instance, HistoryEventType.rule_error, comment=error, detail={"step_key": step_key})
                    raise RuntimeError(error)

                outcome = await _apply_rule_actions(db, instance, rule, context)
                existing_task_ids.add(engine.task_id_str(task))

                if outcome == "auto_approve":
                    engine.complete_task(wf, task.id, {"approved_by": "system", "rule": rule.key})
                    await _record_history(db, instance, HistoryEventType.task_auto_approved, comment=f"Auto-approved by rule '{rule.key}'")
                    progressed = True
                elif outcome == "skip":
                    engine.complete_task(wf, task.id, {"skipped": True})
                    await _record_history(db, instance, HistoryEventType.task_skipped, comment=f"Skipped by rule '{rule.key}'")
                    progressed = True
                else:
                    approver_ids: Set[uuid.UUID] = outcome
                    if not approver_ids:
                        raise RuntimeError(f"Rule '{rule.key}' matched step '{step_key}' but resolved zero approvers.")
                    wf_task = WorkflowTask(
                        instance_id=instance.id, spiff_task_id=engine.task_id_str(task),
                        task_spec_name=engine.task_spec_name(task), task_name=engine.task_bpmn_name(task),
                        step_key=step_key, status=WorkflowTaskStatus.pending,
                        candidate_user_ids=[str(u) for u in approver_ids], rule_id=rule.id,
                        casbin_resource=props.get("casbinResource"), casbin_action=props.get("casbinAction"),
                    )
                    db.add(wf_task)
                    await db.flush()
                    await _record_history(
                        db, instance, HistoryEventType.task_created, task_id=wf_task.id,
                        comment=f"Assigned via rule '{rule.key}'", detail={"candidate_user_ids": [str(u) for u in approver_ids]},
                    )
                    await _notify_candidate_approvers(db, instance, wf_task, approver_ids)
            if not progressed:
                break

        instance.serialized_state = engine.serialize(wf)
        instance.serializer_version = engine.SERIALIZER_VERSION

        if engine.is_completed(wf):
            if engine.is_successful(wf):
                instance.status = InstanceStatus.completed
                instance.result = "approved"
                instance.completed_at = datetime.utcnow()
                await _record_history(db, instance, HistoryEventType.instance_completed, to_status=instance.status.value)
            else:
                instance.status = InstanceStatus.error
                instance.error_detail = "Workflow finished in a non-successful state unexpectedly."
                await _record_history(db, instance, HistoryEventType.instance_error, to_status=instance.status.value, comment=instance.error_detail)
    except Exception as exc:
        instance.status = InstanceStatus.error
        instance.error_detail = str(exc)
        await _record_history(db, instance, HistoryEventType.instance_error, to_status=instance.status.value, comment=str(exc))

    await db.flush()
    return instance


async def start_instance(
    db: AsyncSession, current_user: User, *,
    definition, version: WorkflowVersion,
    business_object_type: str, business_object_id: uuid.UUID, variables: Dict[str, Any],
) -> WorkflowInstance:
    existing = await repositories.get_instance_for_object(db, business_object_type, business_object_id)
    if existing is not None and existing.status == InstanceStatus.running:
        raise HTTPException(status_code=409, detail=f"A workflow is already running for this {business_object_type}.")

    spec_dict, subprocess_dicts = await repositories.get_cached_spec_dicts(version)
    live_spec = engine.spec_from_cache_dict(spec_dict)
    live_subs = {name: engine.spec_from_cache_dict(d) for name, d in subprocess_dicts.items()}
    wf = engine.instantiate(live_spec, live_subs, variables)

    instance = WorkflowInstance(
        instance_code=f"WF-{secrets.token_hex(4).upper()}",
        definition_id=definition.id, version_id=version.id,
        business_object_type=business_object_type, business_object_id=business_object_id,
        status=InstanceStatus.running,
        serialized_state=engine.serialize(wf), serializer_version=engine.SERIALIZER_VERSION,
        started_by=current_user.id,
    )
    db.add(instance)
    await db.flush()
    await _seed_variables(db, instance, variables, actor=current_user)
    await _record_history(db, instance, HistoryEventType.instance_started, actor=current_user, to_status=InstanceStatus.running.value)
    await db.flush()

    instance = await _load_instance_locked(db, instance.id)
    return await advance(db, instance)


async def approve_task(db: AsyncSession, current_user: User, task: WorkflowTask, *, comment: Optional[str], data: Optional[dict]) -> WorkflowInstance:
    if task.status != WorkflowTaskStatus.pending:
        raise HTTPException(status_code=400, detail="This task has already been actioned.")
    instance = await _load_instance_locked(db, task.instance_id)

    task.status = WorkflowTaskStatus.approved
    task.acted_by = current_user.id
    task.acted_at = datetime.utcnow()
    task.comment = comment
    await _record_history(db, instance, HistoryEventType.task_approved, task_id=task.id, actor=current_user, comment=comment)
    await db.flush()

    # Persist any approver-submitted data as instance variables too, not
    # just into Spiff's task.data — completion hooks read exclusively via
    # list_variables_for_instance/WorkflowVariable, which task.set_data()
    # never touches.
    for name, value in (data or {}).items():
        await _upsert_variable(db, instance, name, value)

    spec_dict, subprocess_dicts = await repositories.get_cached_spec_dicts(instance.version)
    wf = engine.deserialize(instance.serialized_state, spec_dict, subprocess_dicts)
    engine.complete_task(wf, uuid.UUID(task.spiff_task_id), {**(data or {}), "approved_by": str(current_user.id)})
    instance.serialized_state = engine.serialize(wf)
    await db.flush()

    return await advance(db, instance)


async def reject_task(db: AsyncSession, current_user: User, task: WorkflowTask, *, comment: Optional[str]) -> WorkflowInstance:
    if task.status != WorkflowTaskStatus.pending:
        raise HTTPException(status_code=400, detail="This task has already been actioned.")
    instance = await _load_instance_locked(db, task.instance_id)

    task.status = WorkflowTaskStatus.rejected
    task.acted_by = current_user.id
    task.acted_at = datetime.utcnow()
    task.comment = comment
    await _record_history(db, instance, HistoryEventType.task_rejected, task_id=task.id, actor=current_user, comment=comment)

    for other in await repositories.list_tasks_for_instance(db, instance.id):
        if other.id != task.id and other.status == WorkflowTaskStatus.pending:
            other.status = WorkflowTaskStatus.cancelled
            await _record_history(db, instance, HistoryEventType.task_cancelled, task_id=other.id, comment="Cancelled — a sibling task was rejected")

    spec_dict, subprocess_dicts = await repositories.get_cached_spec_dicts(instance.version)
    wf = engine.deserialize(instance.serialized_state, spec_dict, subprocess_dicts)
    engine.cancel_workflow(wf)
    instance.serialized_state = engine.serialize(wf)
    instance.status = InstanceStatus.rejected
    instance.result = "rejected"
    instance.completed_at = datetime.utcnow()
    await _record_history(db, instance, HistoryEventType.instance_rejected, actor=current_user, to_status=instance.status.value, comment=comment)
    await db.flush()
    return instance


async def cancel_instance(db: AsyncSession, current_user: User, instance: WorkflowInstance, *, comment: Optional[str]) -> WorkflowInstance:
    instance = await _load_instance_locked(db, instance.id)
    if instance.status != InstanceStatus.running:
        raise HTTPException(status_code=400, detail="Only a running workflow can be cancelled.")

    for t in await repositories.list_tasks_for_instance(db, instance.id):
        if t.status == WorkflowTaskStatus.pending:
            t.status = WorkflowTaskStatus.cancelled
            await _record_history(db, instance, HistoryEventType.task_cancelled, task_id=t.id, actor=current_user, comment=comment)

    spec_dict, subprocess_dicts = await repositories.get_cached_spec_dicts(instance.version)
    wf = engine.deserialize(instance.serialized_state, spec_dict, subprocess_dicts)
    engine.cancel_workflow(wf)
    instance.serialized_state = engine.serialize(wf)
    instance.status = InstanceStatus.cancelled
    instance.result = "cancelled"
    instance.completed_at = datetime.utcnow()
    await _record_history(db, instance, HistoryEventType.instance_cancelled, actor=current_user, to_status=instance.status.value, comment=comment)
    await db.flush()
    return instance


async def reassign_task(db: AsyncSession, current_user: User, task: WorkflowTask, *, new_user_id: uuid.UUID, comment: Optional[str]) -> WorkflowTask:
    if task.status != WorkflowTaskStatus.pending:
        raise HTTPException(status_code=400, detail="This task has already been actioned.")
    instance = await _load_instance_locked(db, task.instance_id)

    old = task.assigned_user_id
    task.assigned_user_id = new_user_id
    if str(new_user_id) not in [str(u) for u in task.candidate_user_ids]:
        task.candidate_user_ids = [*task.candidate_user_ids, str(new_user_id)]
    await _record_history(
        db, instance, HistoryEventType.task_reassigned, task_id=task.id, actor=current_user, comment=comment,
        detail={"from": str(old) if old else None, "to": str(new_user_id)},
    )
    await db.flush()
    return task
