"""BPMN XML -> SpiffWorkflow spec, plus upload-time validation. Ported
near-verbatim from PoultryPro-CBF's app/workflow/parser.py — no branch
concept here to strip.

The only functions in this file that touch SpiffWorkflow's parser API (see
app/workflow/engine.py for the execution/serialization side).

Validated once against the real library (SpiffWorkflow 3.1.2, verified by
hand against a running interpreter):

  - `add_bpmn_str` requires *bytes*: lxml.etree.fromstring raises
    ValueError on a str carrying an XML encoding declaration, which every
    bpmn-js export includes.
  - SpiffWorkflow reserves the task-spec name "Start" for the implicit root
    task it creates in every BpmnProcessSpec.__init__, and "End" for the
    implicit terminal task wired to the process's EndJoin. A BPMN element
    with `id="Start"` or `id="End"` collides with these silently — rejected
    here at validation time so this never reaches a live workflow.
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from lxml import etree
from SpiffWorkflow.bpmn.parser.ValidationException import ValidationException
from SpiffWorkflow.bpmn.specs.bpmn_process_spec import BpmnProcessSpec
from SpiffWorkflow.spiff.parser import SpiffBpmnParser

_RESERVED_TASK_IDS = {"Start", "End"}

_BPMN_NS = "http://www.omg.org/spec/BPMN/20100524/MODEL"
_SPIFF_NS = "http://spiffworkflow.org/bpmn/schema/1.0/core"


@dataclass
class ParsedWorkflow:
    spec: BpmnProcessSpec
    subprocess_specs: Dict[str, BpmnProcessSpec]
    process_id: str


def _make_parser() -> SpiffBpmnParser:
    return SpiffBpmnParser(validator=None)


def list_process_ids(bpmn_xml: str) -> List[str]:
    """Every executable process id contained in a BPMN XML document — used
    by the "import" endpoint to let an admin pick which process to register
    when a file contains more than one."""
    parser = _make_parser()
    parser.add_bpmn_str(bpmn_xml.encode("utf-8"))
    return parser.get_process_ids()


def parse_workflow(bpmn_xml: str, process_id: str) -> ParsedWorkflow:
    """Parse `bpmn_xml` and return the spec for `process_id` plus any
    subprocess specs it depends on. Raises ValueError with a human-readable
    message on any parse failure."""
    parser = _make_parser()
    try:
        parser.add_bpmn_str(bpmn_xml.encode("utf-8"))
        spec = parser.get_spec(process_id)
        subprocess_specs = parser.get_subprocess_specs(process_id)
    except ValidationException as exc:
        raise ValueError(str(exc)) from exc
    except etree.XMLSyntaxError as exc:
        raise ValueError(f"Malformed XML: {exc}") from exc
    return ParsedWorkflow(spec=spec, subprocess_specs=subprocess_specs, process_id=process_id)


@dataclass
class StepInfo:
    task_id: str
    name: Optional[str]
    step_key: Optional[str]
    rule_key: Optional[str]


@dataclass
class ValidationResult:
    errors: List[str] = field(default_factory=list)
    steps: List[StepInfo] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return not self.errors


def validate_bpmn(bpmn_xml: str, process_id: str) -> ValidationResult:
    """Upload-time validation, run before a version is saved or published.
    Structural checks (well-formed XML, exactly the requested process
    present, a start event, no reserved ids) plus safety checks (no
    scriptTask, no pre/postScript — see engine.py's RuleScriptEngine, which
    refuses to execute either) and the one thing this engine actually
    requires: every human task must declare a `stepKey` so the executor can
    look up an approval rule for it."""
    result = ValidationResult()

    try:
        root = etree.fromstring(bpmn_xml.encode("utf-8"))
    except etree.XMLSyntaxError as exc:
        result.errors.append(f"Malformed XML: {exc}")
        return result

    process_nodes = root.findall(f"{{{_BPMN_NS}}}process")
    process_node = next((p for p in process_nodes if p.get("id") == process_id), None)
    if process_node is None:
        found = [p.get("id") for p in process_nodes]
        result.errors.append(
            f"Process '{process_id}' not found in the uploaded XML. Found: {', '.join(found) or 'none'}."
        )
        return result

    if process_node.get("isExecutable", "true").lower() == "false":
        result.errors.append(f"Process '{process_id}' is marked isExecutable=\"false\".")

    start_events = process_node.findall(f"{{{_BPMN_NS}}}startEvent")
    if not start_events:
        result.errors.append("The process has no start event.")

    script_tasks = process_node.findall(f"{{{_BPMN_NS}}}scriptTask")
    if script_tasks:
        result.errors.append(
            f"{len(script_tasks)} scriptTask element(s) found — script tasks are disabled "
            "(admin-authored BPMN cannot execute arbitrary code; use a rule action instead)."
        )

    for tag in ("preScript", "postScript"):
        if process_node.findall(f".//{{{_SPIFF_NS}}}{tag}"):
            result.errors.append(f"spiffworkflow:{tag} found — pre/post scripts are disabled for the same reason as scriptTask.")

    all_ids = [n.get("id") for n in process_node.iter() if n.get("id")]
    reserved_hits = sorted(set(all_ids) & _RESERVED_TASK_IDS)
    if reserved_hits:
        result.errors.append(
            f"Element id(s) {', '.join(reserved_hits)} are reserved by SpiffWorkflow's engine "
            "(every process has an implicit 'Start' and 'End' task) — rename these elements. "
            "Using a reserved id doesn't raise an error at parse time, it silently drops that "
            "element's connections, so this is checked explicitly here."
        )

    for tag in ("userTask", "manualTask"):
        for node in process_node.findall(f"{{{_BPMN_NS}}}{tag}"):
            node_id = node.get("id")
            node_name = node.get("name")
            props = {
                p.get("name"): p.get("value")
                for p in node.findall(f".//{{{_SPIFF_NS}}}properties/{{{_SPIFF_NS}}}property")
            }
            step_key = props.get("stepKey")
            if not step_key:
                result.errors.append(
                    f"Task '{node_name or node_id}' ({node_id}) has no stepKey property — "
                    "every human task needs one so an approval rule can be resolved for it."
                )
            result.steps.append(StepInfo(task_id=node_id, name=node_name, step_key=step_key, rule_key=props.get("ruleKey")))

    if not result.errors:
        try:
            parse_workflow(bpmn_xml, process_id)
        except ValueError as exc:
            result.errors.append(str(exc))

    return result
