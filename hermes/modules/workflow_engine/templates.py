"""Generic, reusable workflow templates -- structural shapes only, no
business-specific logic. Each builder returns a `WorkflowDefinition`
parameterized purely by step names/kinds, demonstrating the engine's
capabilities (sequencing, parallelism, approval gates) without encoding
any particular business process. Real, meaningful step logic (tool
calls, memory ops) can be layered on by passing `kind`/further fields
per step -- these builders default every step to a harmless `noop`.
"""
from __future__ import annotations

from hermes.modules.workflow_engine.models import StepDefinition, WorkflowDefinition


def sequential_template(
    workflow_id: str, name: str, step_names: list[str], *, kind: str = "noop"
) -> WorkflowDefinition:
    """A straight-line chain: each step depends on the one directly
    before it. Demonstrates step sequencing."""
    steps = [
        StepDefinition(name=step_name, kind=kind, depends_on=[step_names[i - 1]] if i > 0 else [])
        for i, step_name in enumerate(step_names)
    ]
    return WorkflowDefinition(workflow_id=workflow_id, name=name, steps=steps)


def fan_out_fan_in_template(
    workflow_id: str, name: str, *, parallel_step_names: list[str], join_step_name: str, kind: str = "noop"
) -> WorkflowDefinition:
    """N independent steps with no dependency on each other -- so the
    scheduler runs them concurrently -- joined by one step that depends
    on all of them. Demonstrates parallel steps."""
    steps = [StepDefinition(name=n, kind=kind) for n in parallel_step_names]
    steps.append(StepDefinition(name=join_step_name, kind=kind, depends_on=list(parallel_step_names)))
    return WorkflowDefinition(workflow_id=workflow_id, name=name, steps=steps)


def approval_gated_template(
    workflow_id: str,
    name: str,
    *,
    before_step_name: str,
    approval_step_name: str,
    after_step_name: str,
    approval_message: str = "Approval required to continue.",
    kind: str = "noop",
) -> WorkflowDefinition:
    """A three-step chain with a human approval gate in the middle.
    Demonstrates human approval gates."""
    steps = [
        StepDefinition(name=before_step_name, kind=kind),
        StepDefinition(
            name=approval_step_name,
            kind="approval",
            depends_on=[before_step_name],
            approval_message=approval_message,
        ),
        StepDefinition(name=after_step_name, kind=kind, depends_on=[approval_step_name]),
    ]
    return WorkflowDefinition(workflow_id=workflow_id, name=name, steps=steps)
