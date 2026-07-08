from hermes.modules.workflow_engine.templates import (
    approval_gated_template,
    fan_out_fan_in_template,
    sequential_template,
)


def test_sequential_template_chains_each_step_to_the_previous():
    definition = sequential_template("wf1", "Sequential", ["a", "b", "c"])

    by_name = {s.name: s for s in definition.steps}
    assert by_name["a"].depends_on == []
    assert by_name["b"].depends_on == ["a"]
    assert by_name["c"].depends_on == ["b"]


def test_fan_out_fan_in_template_joins_all_parallel_steps():
    definition = fan_out_fan_in_template(
        "wf1", "FanOutIn", parallel_step_names=["p1", "p2", "p3"], join_step_name="join"
    )

    by_name = {s.name: s for s in definition.steps}
    assert by_name["p1"].depends_on == []
    assert by_name["p2"].depends_on == []
    assert set(by_name["join"].depends_on) == {"p1", "p2", "p3"}


def test_approval_gated_template_puts_approval_kind_in_the_middle():
    definition = approval_gated_template(
        "wf1", "Gated", before_step_name="before", approval_step_name="gate", after_step_name="after"
    )

    by_name = {s.name: s for s in definition.steps}
    assert by_name["gate"].kind == "approval"
    assert by_name["gate"].depends_on == ["before"]
    assert by_name["after"].depends_on == ["gate"]
