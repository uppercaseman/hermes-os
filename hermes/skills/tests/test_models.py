from hermes.skills.models import SkillManifest


def test_manifest_defaults_to_empty_requirements():
    manifest = SkillManifest(name="x", description="d", entrypoint="pkg.mod:Cls")

    assert manifest.required_capabilities == []
    assert manifest.required_tools == []
    assert manifest.version == "0.1.0"
