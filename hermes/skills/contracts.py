"""Protocol every runnable Skill implementation satisfies.

A Skill is a reusable, composed capability shared across agents and
workflows -- typically built by combining one or more Capability
Registry selections and Tool Manager invocations into a single, named
unit of work (e.g. "web_search" might resolve `browser_automation` and
`reasoning` capabilities and combine their results). Only the manifest
and this Protocol are infrastructure here; no skill in this codebase has
a real implementation yet -- see the `skill.toml` placeholder alongside
each example skill directory.
"""
from __future__ import annotations

from typing import Protocol

from hermes.skills.models import SkillManifest, SkillRequest, SkillResult


class Skill(Protocol):
    manifest: SkillManifest

    async def run(self, request: SkillRequest) -> SkillResult: ...
