"""Test double satisfying the Skill protocol -- not a real skill
implementation, used only to exercise SkillRegistry's registration logic.
"""
from __future__ import annotations

from hermes.skills.models import SkillManifest, SkillRequest, SkillResult


class FakeSkill:
    def __init__(self, name: str = "fake_skill") -> None:
        self.manifest = SkillManifest(
            name=name,
            description="A fake skill for tests.",
            entrypoint="hermes.skills.tests.fakes:FakeSkill",
            required_capabilities=["reasoning"],
        )
        self.run_calls = 0

    async def run(self, request: SkillRequest) -> SkillResult:
        self.run_calls += 1
        return SkillResult(skill_name=self.manifest.name, correlation_id=request.correlation_id, status="completed")
