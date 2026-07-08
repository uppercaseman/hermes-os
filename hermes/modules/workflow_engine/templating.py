"""Pure, safe template resolution for step parameters/values.

No `eval`/`exec` -- only two reference forms are understood:
`{{input.<path>}}` (the run's initial input) and
`{{steps.<step_name>.output.<path>}}` (a prior step's output), resolved
by plain dict traversal. A template that is the WHOLE string preserves
the referenced value's original type (e.g. a number stays a number); a
template embedded inside a larger string is stringified and substituted
in place. An unresolvable path returns `None` rather than raising --
templating a typo should not crash a step; the step will simply see
`None` where it expected a value.
"""
from __future__ import annotations

import re
from typing import Any

_TEMPLATE_RE = re.compile(r"\{\{\s*([\w\.]+)\s*\}\}")


def resolve_templates(value: Any, *, input: dict[str, Any], step_outputs: dict[str, dict[str, Any] | None]) -> Any:
    if isinstance(value, str):
        stripped = value.strip()
        whole_match = _TEMPLATE_RE.fullmatch(stripped)
        if whole_match:
            return _resolve_path(whole_match.group(1), input=input, step_outputs=step_outputs)
        return _TEMPLATE_RE.sub(
            lambda m: str(_resolve_path(m.group(1), input=input, step_outputs=step_outputs)), value
        )
    if isinstance(value, dict):
        return {k: resolve_templates(v, input=input, step_outputs=step_outputs) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_templates(v, input=input, step_outputs=step_outputs) for v in value]
    return value


def _resolve_path(path: str, *, input: dict[str, Any], step_outputs: dict[str, dict[str, Any] | None]) -> Any:
    parts = path.split(".")
    node: Any
    if parts[0] == "input":
        node = input
        parts = parts[1:]
    elif parts[0] == "steps" and len(parts) >= 3 and parts[2] == "output":
        node = step_outputs.get(parts[1]) or {}
        parts = parts[3:]
    else:
        return None
    for part in parts:
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node
