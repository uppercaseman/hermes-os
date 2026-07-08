from pathlib import Path

import pytest

from hermes.skills.interface import build_skill_registry

SKILLS_ROOT = Path(__file__).resolve().parent.parent  # hermes/skills/


@pytest.fixture
def registry():
    return build_skill_registry()


@pytest.fixture
def skills_root() -> Path:
    return SKILLS_ROOT
