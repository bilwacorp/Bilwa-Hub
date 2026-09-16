"""Adapter around SpiffWorkflow's runtime API — every direct call into the
library for execution/serialization lives here (see parser.py for the
parsing half). Ported near-verbatim from PoultryPro-CBF's
app/workflow/engine.py — no branch concept here to strip, this file is
already generic.

Gotchas this file works around (each verified by hand against a running
SpiffWorkflow 3.1.2 interpreter — most online examples target the pre-3.0
API, which no longer exists):

  - `workflow.data` and `task.data` are separate namespaces; SpiffWorkflow
    never propagates the former into the latter. Instance variables must be
    seeded on the root task's data (`wf.task_tree.set_data(...)`), not
    `wf.data`, or gateway conditions and every task's inherited data will
    silently not see them.
  - `to_dict()` embeds the *entire spec* by default (20-100KB of redundant
    JSON per instance) and does not stamp a version; `from_dict()` does not
    migrate. We store state only and stamp/migrate by hand.
  - `script_engine` is not part of the serialized state — it must be
    reassigned after every deserialize.
  - Live `BpmnProcessSpec` objects are mutated at runtime, so specs are
    cached as plain dicts and restored into a private object per instance —
    never shared as live objects across instances.
"""
import copy
from typing import Any, Dict, List, Optional

from SpiffWorkflow import TaskState
from SpiffWorkflow.bpmn import BpmnWorkflow
from SpiffWorkflow.bpmn.script_engine import PythonScriptEngine
from SpiffWorkflow.bpmn.serializer import BpmnWorkflowSerializer
from SpiffWorkflow.spiff.serializer import DEFAULT_CONFIG as SPIFF_CONFIG

from app.rules.evaluator import evaluate_bool

_registry = BpmnWorkflowSerializer.configure(SPIFF_CONFIG)
_serializer = BpmnWorkflowSerializer(registry=_registry)

SERIALIZER_VERSION = _serializer.VERSION


class RuleScriptEngine(PythonScriptEngine):
    """Replaces SpiffWorkflow's default eval()/exec()-based evaluator —
    admin-authored BPMN here is effectively admin-authored code, so that is
    not acceptable.

    `evaluate` is the sole hook `_BpmnCondition._matches` uses for every
    gateway condition — delegating it to rule-engine means gateways and
    approval rules share one safe, sandboxed expression language, with no
    eval()/exec() path reachable from stored BPMN. `execute` always raises:
    bpmn:scriptTask and spiffworkflow:pre/postScript are rejected at upload
    validation (parser.py), so this should never be reached — refusing here
    too is defense in depth for a version saved before that check existed.
    """

    def evaluate(self, task, expression, external_context=None):
        context = dict(task.data)
        if external_context:
            context.update(external_context)
        return evaluate_bool(expression, context)

    def execute(self, task, script, external_context=None, external_methods=None):
        raise NotImplementedError("bpmn:scriptTask / pre/postScript are disabled — use a rule action instead.")


def new_script_engine() -> RuleScriptEngine:
    return RuleScriptEngine()


def spec_to_cache_dict(spec) -> dict:
    """Convert a freshly-parsed live spec to the plain-dict form the version
    cache stores. See module docstring — never cache the live object."""
    return _registry.convert(spec)


def spec_from_cache_dict(spec_dict: dict):
    """Restore a private, mutation-safe live spec object from a cached dict.
    Used only when starting a *new* instance."""
    return _registry.restore(copy.deepcopy(spec_dict))


def instantiate(spec, subprocess_specs: dict, variables: Optional[Dict[str, Any]] = None) -> BpmnWorkflow:
    """Start a brand-new workflow instance from a live spec. `variables` are
    seeded on the task tree's root task data so every gateway condition and
    every task's inherited data can see them from step one."""
    wf = BpmnWorkflow(spec, subprocess_specs or {}, script_engine=new_script_engine())
    if variables:
        wf.task_tree.set_data(**variables)
    return wf


def run_engine_steps(wf: BpmnWorkflow) -> None:
    """Advance every non-human task (gateways, automatic tasks, timers)
    until the workflow blocks on a human task or completes."""
    wf.do_engine_steps()
    wf.refresh_waiting_tasks()


def ready_human_tasks(wf: BpmnWorkflow) -> List:
    return list(wf.get_tasks(state=TaskState.READY, manual=True))


def complete_task(wf: BpmnWorkflow, task_id, data: Optional[Dict[str, Any]] = None) -> None:
    task = wf.get_task_from_id(task_id)
    if data:
        task.set_data(**data)
    task.run()


def cancel_workflow(wf: BpmnWorkflow) -> None:
    wf.cancel()


def is_completed(wf: BpmnWorkflow) -> bool:
    return wf.is_completed()


def is_successful(wf: BpmnWorkflow) -> bool:
    """False after wf.cancel() or an error; True on a normal completion."""
    return bool(wf.success)


def task_extension_properties(task) -> Dict[str, str]:
    return dict(task.task_spec.extensions.get("properties", {}) or {})


def task_spec_name(task) -> str:
    return task.task_spec.name


def task_bpmn_name(task) -> Optional[str]:
    return task.task_spec.bpmn_name


def task_id_str(task) -> str:
    return str(task.id)


def serialize(wf: BpmnWorkflow) -> dict:
    """State-only serialization: strip the embedded spec/subprocess_specs
    and stamp the serializer version."""
    dct = _serializer.to_dict(wf)
    dct.pop("spec", None)
    dct.pop("subprocess_specs", None)
    dct[_serializer.VERSION_KEY] = _serializer.VERSION
    return dct


def deserialize(state: dict, spec_dict: dict, subprocess_specs_dict: Optional[dict] = None) -> BpmnWorkflow:
    """Rehydrate a workflow from state-only JSONB plus the *exact* spec dict
    the instance was started against (from the version cache) — never a
    re-parsed "latest" version.

    `spec_dict`/`subprocess_specs_dict` come from the shared, reused version
    cache — `from_dict` consumes/mutates the dict it's given, so these must
    be deep-copied before splicing in, or the cache entry gets corrupted for
    the next instance that resumes against the same version.
    """
    dct = copy.deepcopy(state)
    dct["spec"] = copy.deepcopy(spec_dict)
    dct["subprocess_specs"] = copy.deepcopy(subprocess_specs_dict) if subprocess_specs_dict else {}
    if _serializer.VERSION_KEY in dct:
        _serializer.migrate(dct)
    wf = _serializer.from_dict(dct)
    wf.script_engine = new_script_engine()  # not part of serialized state — must be reassigned every time
    return wf
