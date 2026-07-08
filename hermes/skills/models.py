"""Pydantic data contracts for Hermes Skills."""
from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field


class SkillManifest(BaseModel):
    """Declarative description of one skill -- what a `skill.toml` file
    parses into. Purely metadata: registering or discovering a manifest
    never imports or executes the code `entrypoint` refers to.

    `required_capabilities` should be names from
    `capability_registry.capabilities` (e.g. "reasoning",
    "browser_automation") -- a skill declares CAPABILITIES it needs, never
    a specific provider, the same principle the Capability Registry
    itself enforces. `required_tools` exists for the rare case a skill
    genuinely needs a named tool rather than a capability-routed one, and
    is deliberately empty on every example in this codebase.
    """

    name: str
    version: str = "0.1.0"
    description: str
    entrypoint: str = Field(description='"package.module:ClassName" reference -- never imported by the registry.')
    required_capabilities: list[str] = Field(default_factory=list)
    required_tools: list[str] = Field(default_factory=list)


class SkillRequest(BaseModel):
    skill_name: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    correlation_id: uuid.UUID = Field(default_factory=uuid.uuid4)


class SkillResult(BaseModel):
    skill_name: str
    correlation_id: uuid.UUID
    status: Literal["completed", "failed"]
    output: dict[str, Any] | None = None
    error: str | None = None
