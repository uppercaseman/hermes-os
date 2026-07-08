from hermes.modules.workflow_engine.interface import build_workflow_engine
from hermes.demos.research_brief.workflow import RESEARCH_BRIEF_WORKFLOW_ID, build_research_brief_workflow


def test_workflow_has_five_steps_matching_the_spec():
    definition = build_research_brief_workflow()

    names = [s.name for s in definition.steps]
    assert names == ["accept_topic", "read_memory", "call_research_tool", "save_to_memory", "assemble_brief"]


def test_workflow_steps_are_purely_sequential():
    definition = build_research_brief_workflow()
    by_name = {s.name: s for s in definition.steps}

    assert by_name["accept_topic"].depends_on == []
    assert by_name["read_memory"].depends_on == ["accept_topic"]
    assert by_name["call_research_tool"].depends_on == ["read_memory"]
    assert by_name["save_to_memory"].depends_on == ["call_research_tool"]
    assert by_name["assemble_brief"].depends_on == ["save_to_memory"]


def test_workflow_passes_the_real_engines_own_validation():
    """Registers with a real WorkflowEngine -- if the definition were
    malformed (bad dependency, missing tool_name, missing memory key),
    this would raise InvalidWorkflowDefinitionError."""
    engine = build_workflow_engine()
    engine.register_workflow(build_research_brief_workflow())

    assert engine.get_workflow(RESEARCH_BRIEF_WORKFLOW_ID).workflow_id == RESEARCH_BRIEF_WORKFLOW_ID


def test_tool_call_step_names_the_mock_tool_directly_not_a_capability():
    definition = build_research_brief_workflow()
    tool_step = next(s for s in definition.steps if s.name == "call_research_tool")

    assert tool_step.tool_name == "mock_research"
    assert tool_step.capability is None


def test_memory_steps_use_a_topic_templated_key_not_a_fixed_one():
    definition = build_research_brief_workflow()
    read_step = next(s for s in definition.steps if s.name == "read_memory")
    write_step = next(s for s in definition.steps if s.name == "save_to_memory")

    assert read_step.memory_key == "research_brief/{{input.topic}}"
    assert write_step.memory_key == read_step.memory_key  # read and write must agree on the same template
