import pytest

from hermes.skills.errors import DuplicateSkillError, InvalidManifestError, UnknownSkillError
from hermes.skills.models import SkillManifest, SkillRequest
from hermes.skills.service import SkillRegistry
from hermes.skills.tests.fakes import FakeSkill

EXPECTED_EXAMPLE_SKILLS = {
    "web_search",
    "code_review",
    "email_drafting",
    "image_generation",
    "document_analysis",
}


def _manifest(name: str) -> SkillManifest:
    return SkillManifest(name=name, description="d", entrypoint="pkg.mod:Cls")


def test_register_manifest_then_get_manifest(registry):
    registry.register_manifest(_manifest("web_search"))

    manifest = registry.get_manifest("web_search")
    assert manifest.name == "web_search"


def test_duplicate_manifest_raises(registry):
    registry.register_manifest(_manifest("web_search"))

    with pytest.raises(DuplicateSkillError):
        registry.register_manifest(_manifest("web_search"))


def test_unknown_manifest_raises(registry):
    with pytest.raises(UnknownSkillError):
        registry.get_manifest("nope")


def test_register_skill_registers_its_manifest_automatically(registry):
    skill = FakeSkill(name="fake_skill")

    registry.register_skill(skill)

    assert registry.get_manifest("fake_skill").name == "fake_skill"
    assert registry.get_skill("fake_skill") is skill
    assert registry.has_runnable_implementation("fake_skill") is True


def test_manifest_without_a_runnable_skill_is_advertised_but_not_runnable(registry):
    registry.register_manifest(_manifest("web_search"))

    assert registry.has_runnable_implementation("web_search") is False
    with pytest.raises(UnknownSkillError):
        registry.get_skill("web_search")


def test_duplicate_skill_registration_raises(registry):
    registry.register_skill(FakeSkill(name="fake_skill"))

    with pytest.raises(DuplicateSkillError):
        registry.register_skill(FakeSkill(name="fake_skill"))


def test_list_manifests_returns_everything_registered(registry):
    registry.register_manifest(_manifest("a"))
    registry.register_manifest(_manifest("b"))

    names = {m.name for m in registry.list_manifests()}
    assert names == {"a", "b"}


async def test_fake_skill_run_returns_a_completed_result(registry):
    skill = FakeSkill(name="fake_skill")
    registry.register_skill(skill)

    result = await registry.get_skill("fake_skill").run(SkillRequest(skill_name="fake_skill"))

    assert result.status == "completed"
    assert skill.run_calls == 1


# --------------------------------------------------------------------- #
# Manifest file loading + directory discovery
# --------------------------------------------------------------------- #

def test_load_manifest_parses_a_real_skill_toml(skills_root):
    manifest = SkillRegistry.load_manifest(skills_root / "web_search" / "skill.toml")

    assert manifest.name == "web_search"
    assert "browser_automation" in manifest.required_capabilities
    assert manifest.required_tools == []


def test_load_manifest_raises_for_malformed_toml(tmp_path):
    bad = tmp_path / "skill.toml"
    bad.write_text("[skill]\nname = \"missing_description\"\n")  # description is required

    with pytest.raises(InvalidManifestError):
        SkillRegistry.load_manifest(bad)


def test_discover_finds_all_five_example_skills(registry, skills_root):
    manifests = registry.discover(skills_root)

    assert {m.name for m in manifests} == EXPECTED_EXAMPLE_SKILLS
    assert {m.name for m in registry.list_manifests()} == EXPECTED_EXAMPLE_SKILLS


def test_discover_skips_directories_without_a_manifest(registry, skills_root):
    # `tests/` sits alongside the example skill directories and has no
    # skill.toml -- discover() must not choke on it.
    manifests = registry.discover(skills_root)

    assert "tests" not in {m.name for m in manifests}


def test_discover_does_not_duplicate_already_registered_manifests(registry, skills_root):
    registry.register_manifest(_manifest("web_search"))  # pre-register with different content

    manifests = registry.discover(skills_root)

    # discover() must not raise DuplicateSkillError for a name it already
    # knows -- it simply doesn't overwrite it.
    assert registry.get_manifest("web_search").entrypoint == "pkg.mod:Cls"
    assert len([m for m in manifests if m.name == "web_search"]) == 1


def test_every_example_skill_declares_capabilities_not_specific_tools(registry, skills_root):
    """Consistency check with the Capability Registry's core principle:
    a skill should request capabilities, never a specific provider/tool."""
    manifests = registry.discover(skills_root)

    for manifest in manifests:
        assert manifest.required_capabilities, f"{manifest.name} declares no capabilities"
        assert manifest.required_tools == [], f"{manifest.name} hardcodes a specific tool"
