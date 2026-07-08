from hermes.modules.workflow_engine.templating import resolve_templates


def test_whole_string_template_preserves_original_type():
    result = resolve_templates("{{input.count}}", input={"count": 42}, step_outputs={})

    assert result == 42
    assert isinstance(result, int)


def test_partial_template_is_stringified_and_substituted():
    result = resolve_templates("Hello {{input.name}}!", input={"name": "World"}, step_outputs={})

    assert result == "Hello World!"


def test_resolves_a_prior_step_output():
    result = resolve_templates(
        "{{steps.search.output.query}}", input={}, step_outputs={"search": {"query": "hermes os"}}
    )

    assert result == "hermes os"


def test_unresolvable_path_returns_none_rather_than_raising():
    result = resolve_templates("{{input.missing.deeply.nested}}", input={}, step_outputs={})

    assert result is None


def test_recurses_into_dicts_and_lists():
    template = {"a": "{{input.x}}", "b": ["{{input.y}}", "literal"]}

    result = resolve_templates(template, input={"x": 1, "y": 2}, step_outputs={})

    assert result == {"a": 1, "b": [2, "literal"]}


def test_non_template_values_pass_through_unchanged():
    assert resolve_templates(5, input={}, step_outputs={}) == 5
    assert resolve_templates(None, input={}, step_outputs={}) is None


def test_step_with_no_output_yet_resolves_to_none():
    result = resolve_templates("{{steps.never_ran.output.x}}", input={}, step_outputs={"never_ran": None})

    assert result is None
