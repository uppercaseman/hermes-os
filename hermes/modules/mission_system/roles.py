"""Built-in specialist role templates.

These are TEMPORARY mission roles, not specialist agents -- no AI/model
logic here. A `RoleTemplate` is a reusable, structured permission
profile the Team Builder matches against a mission's required
capabilities; the six examples below are defaults a caller can add to
or override, not a closed set (see `TeamBuilder.register_template`).

`trigger_capabilities` decides when a role is inferred automatically
(any overlap with the mission's `required_capabilities` pulls the role
in). Research Specialist and Developer have genuinely distinguishing
triggers (`browser_automation`/`memory`, `code_generation`).
Reviewer, Architect, Content Writer, and QA don't have a capability in
the current vocabulary (capability_registry.capabilities) that's
uniquely theirs rather than shared with every other reasoning-heavy
role -- giving them a trigger would just mean they get pulled in
alongside Developer/Research Specialist on every mission that needs
"reasoning", which isn't meaningful inference. They're deliberately
explicit-request-only roles (via `Mission.requested_roles`); this is a
modeling choice, not an oversight, and it's exactly what
`requested_roles` exists for.

ADR 0019 (Sprint 0): the Research Specialist triggers/uses the canonical
`memory` capability (per ADR 0016's 12-capability taxonomy). The legacy
`memory_search` constant is kept exported as an alias for backward
compatibility but is no longer referenced inside Hermes itself.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from hermes.modules.capability_registry.capabilities import (
    BROWSER_AUTOMATION,
    CODE_GENERATION,
    MEMORY,
    REASONING,
)


class RoleTemplate(BaseModel):
    role_name: str
    trigger_capabilities: list[str] = Field(default_factory=list)
    default_capabilities: list[str] = Field(default_factory=list)
    default_tools: list[str] = Field(default_factory=list)
    default_memory_scopes: list[str] = Field(default_factory=list)


DEFAULT_ROLE_TEMPLATES: list[RoleTemplate] = [
    RoleTemplate(
        role_name="Research Specialist",
        trigger_capabilities=[BROWSER_AUTOMATION, MEMORY],
        default_capabilities=[REASONING, BROWSER_AUTOMATION, MEMORY],
        default_memory_scopes=["session", "persistent"],
    ),
    RoleTemplate(
        role_name="Developer",
        trigger_capabilities=[CODE_GENERATION],
        default_capabilities=[REASONING, CODE_GENERATION],
        default_memory_scopes=["workflow", "session"],
    ),
    RoleTemplate(
        role_name="Reviewer",
        default_capabilities=[REASONING, CODE_GENERATION],
        default_memory_scopes=["workflow"],
    ),
    RoleTemplate(
        role_name="Architect",
        default_capabilities=[REASONING],
        default_memory_scopes=["persistent", "workflow"],
    ),
    RoleTemplate(
        role_name="Content Writer",
        default_capabilities=[REASONING],
        default_memory_scopes=["session", "persistent"],
    ),
    RoleTemplate(
        role_name="QA",
        default_capabilities=[REASONING, CODE_GENERATION],
        default_memory_scopes=["workflow"],
    ),
]
