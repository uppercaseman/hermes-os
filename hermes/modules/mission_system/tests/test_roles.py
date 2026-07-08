from hermes.modules.mission_system.roles import DEFAULT_ROLE_TEMPLATES


def test_all_six_example_roles_are_registered_by_default():
    names = {t.role_name for t in DEFAULT_ROLE_TEMPLATES}

    assert names == {"Research Specialist", "Developer", "Reviewer", "Architect", "Content Writer", "QA"}


def test_research_specialist_and_developer_have_distinguishing_triggers():
    by_name = {t.role_name: t for t in DEFAULT_ROLE_TEMPLATES}

    assert "browser_automation" in by_name["Research Specialist"].trigger_capabilities
    assert "memory" in by_name["Research Specialist"].trigger_capabilities
    assert "code_generation" in by_name["Developer"].trigger_capabilities


def test_explicit_only_roles_have_no_auto_trigger():
    by_name = {t.role_name: t for t in DEFAULT_ROLE_TEMPLATES}

    for role_name in ("Reviewer", "Architect", "Content Writer", "QA"):
        assert by_name[role_name].trigger_capabilities == []
