"""SkillRegistry -- discovers, validates, and holds Skill manifests and
(optionally) live Skill implementations.

This is deliberately the thin, declarative half of the "future plugin
system" described in the original architecture doc: it discovers and
validates `skill.toml` manifests without ever importing or executing the
code an `entrypoint` string refers to. Dynamically loading and
sandboxing an entrypoint is future work; this registry only proves a
manifest is well-formed and lets an already in-process `Skill` object be
registered against it.
"""
from __future__ import annotations

import tomllib
from pathlib import Path

from hermes.skills.contracts import Skill
from hermes.skills.errors import DuplicateSkillError, InvalidManifestError, UnknownSkillError
from hermes.skills.models import SkillManifest

MANIFEST_FILENAME = "skill.toml"


class SkillRegistry:
    def __init__(self) -> None:
        self._manifests: dict[str, SkillManifest] = {}
        self._skills: dict[str, Skill] = {}

    def register_manifest(self, manifest: SkillManifest) -> None:
        """Registers a manifest with no runnable implementation behind it
        yet -- enough to advertise a skill's existence and requirements
        before (or without ever) wiring in real code."""
        if manifest.name in self._manifests:
            raise DuplicateSkillError(manifest.name)
        self._manifests[manifest.name] = manifest

    def register_skill(self, skill: Skill) -> None:
        """Registers a live, runnable Skill. Its manifest is registered
        alongside it automatically if not already present."""
        if skill.manifest.name not in self._manifests:
            self.register_manifest(skill.manifest)
        if skill.manifest.name in self._skills:
            raise DuplicateSkillError(skill.manifest.name)
        self._skills[skill.manifest.name] = skill

    def get_manifest(self, name: str) -> SkillManifest:
        if name not in self._manifests:
            raise UnknownSkillError(name)
        return self._manifests[name]

    def get_skill(self, name: str) -> Skill:
        if name not in self._skills:
            raise UnknownSkillError(name)
        return self._skills[name]

    def list_manifests(self) -> list[SkillManifest]:
        return list(self._manifests.values())

    def has_runnable_implementation(self, name: str) -> bool:
        return name in self._skills

    @staticmethod
    def load_manifest(path: Path) -> SkillManifest:
        """Parses and validates one `skill.toml` file. Never imports the
        entrypoint it declares -- loading is pure data validation."""
        try:
            with path.open("rb") as f:
                data = tomllib.load(f)
            skill_section = data["skill"]
            requirements = data.get("requirements", {})
            return SkillManifest(
                name=skill_section["name"],
                version=skill_section.get("version", "0.1.0"),
                description=skill_section["description"],
                entrypoint=skill_section["entrypoint"],
                required_capabilities=requirements.get("capabilities", []),
                required_tools=requirements.get("tools", []),
            )
        except Exception as exc:  # noqa: BLE001 -- any parse/validation issue becomes one clear error
            raise InvalidManifestError(path, str(exc)) from exc

    def discover(self, skills_root: Path) -> list[SkillManifest]:
        """Scans `skills_root/*/skill.toml`, validates and registers
        each one found. Directories without a `skill.toml` (e.g.
        `tests/`, `__pycache__`) are silently skipped. Returns the
        manifests discovered, in directory-name order."""
        discovered: list[SkillManifest] = []
        for entry in sorted(skills_root.iterdir()):
            manifest_path = entry / MANIFEST_FILENAME
            if not manifest_path.is_file():
                continue
            manifest = self.load_manifest(manifest_path)
            if manifest.name not in self._manifests:
                self.register_manifest(manifest)
            discovered.append(manifest)
        return discovered
