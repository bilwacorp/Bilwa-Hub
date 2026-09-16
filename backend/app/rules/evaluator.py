"""rule-engine wrapper — the one safe, sandboxed expression language shared
by approval-rule conditions (rules/resolvers.py, rules/services.py) and
BPMN gateway conditions (app/workflow/engine.py's RuleScriptEngine).
Ported verbatim from PoultryPro-CBF's app/rules/evaluator.py. rule-engine
is a PLY-generated grammar with no eval()/exec() path, which is what makes
it safe for admin-authored input in the first place.

The context here is intentionally untyped (no `type_resolver`): rules are
generic across whatever business object started the instance, each with
its own variable set. `Rule.is_valid()` without a type resolver still
catches genuine syntax errors at save time — just not "this symbol doesn't
exist for this context", which is checked at evaluate time instead (a
missing symbol is a SymbolResolutionError, caught by evaluate_safe below
and treated as a non-match rather than a 500).
"""
from typing import Optional, Tuple

import rule_engine

# mapping_attribute_lookup=False: turns `foo.bar` dot-access on plain dicts
# into a parse-time error instead of relying on deprecated behavior. Rules
# use `foo["bar"]`-style item access instead (rule-engine's default
# `resolve_item` resolver) — we always pass plain dicts as context, never
# ORM instances, so no object-attribute surface is exposed to
# admin-authored expressions.
DEFAULT_CONTEXT = rule_engine.Context(mapping_attribute_lookup=False, default_timezone="utc")


def is_valid_expression(expression: str, context: rule_engine.Context = DEFAULT_CONTEXT) -> bool:
    return rule_engine.Rule.is_valid(expression, context=context)


def validate_expression(expression: str, context: rule_engine.Context = DEFAULT_CONTEXT) -> Optional[str]:
    """Returns None if valid, else a human-readable error message."""
    try:
        rule_engine.Rule(expression, context=context)
        return None
    except rule_engine.EngineError as exc:
        return str(exc)


def evaluate_bool(expression: str, values: dict, context: rule_engine.Context = DEFAULT_CONTEXT) -> bool:
    """Evaluate `expression` against `values` and coerce to bool. Used
    directly by BPMN gateway conditions, where an error should propagate —
    a broken gateway condition is a workflow authoring bug, not a routing
    outcome, and the executor's caller wraps the whole advance loop to turn
    it into instance.status = 'error' with full detail. Approval-rule
    conditions instead go through evaluate_safe below, which never raises."""
    return bool(rule_engine.Rule(expression, context=context).matches(values))


def evaluate_safe(expression: str, values: dict, context: rule_engine.Context = DEFAULT_CONTEXT) -> Tuple[bool, Optional[str]]:
    """Like evaluate_bool, but never raises — returns (matched, error).
    Used when evaluating an approval-rule's conditions: a rule authoring
    mistake must not crash an approve/reject request. The caller treats a
    non-None error as "this rule doesn't match" and records a rule_error
    history entry rather than surfacing a 500."""
    try:
        return evaluate_bool(expression, values, context=context), None
    except rule_engine.EngineError as exc:
        return False, str(exc)
