"""Skill-registry-specific exception types."""
from __future__ import annotations

from pathlib import Path


class InvalidManifestError(Exception):
    def __init__(self, path: Path, reason: str) -> None:
        self.path = path
        self.reason = reason
        super().__init__(f"invalid skill manifest at {path}: {reason}")


class DuplicateSkillError(Exception):
    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"a skill named {name!r} is already registered")


class UnknownSkillError(Exception):
    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"no skill named {name!r} is registered")
