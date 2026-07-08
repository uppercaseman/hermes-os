"""Public entry point for Hermes Skills.

Everything outside this package imports from here, never from service.py
directly -- mirrors every other module's interface.py convention.
"""
from __future__ import annotations

from hermes.skills.contracts import Skill
from hermes.skills.errors import DuplicateSkillError, InvalidManifestError, UnknownSkillError
from hermes.skills.models import SkillManifest, SkillRequest, SkillResult
from hermes.skills.service import SkillRegistry

__all__ = [
    "SkillRegistry",
    "Skill",
    "SkillManifest",
    "SkillRequest",
    "SkillResult",
    "DuplicateSkillError",
    "InvalidManifestError",
    "UnknownSkillError",
    "build_skill_registry",
]


def build_skill_registry() -> SkillRegistry:
    return SkillRegistry()
