from hermes.modules.logging_system.severity import classify_severity


def test_failed_events_classify_as_error():
    assert classify_severity("commander.run.failed", "info") == "error"
    assert classify_severity("task.failed", "info") == "error"
    assert classify_severity("workflow_engine.step.failed", "info") == "error"


def test_dead_letter_and_crash_events_classify_as_error():
    assert classify_severity("task_queue.task.dead_lettered", "info") == "error"
    assert classify_severity("supervisor.unit.crashed", "info") == "error"


def test_denied_and_unavailable_classify_as_error():
    assert classify_severity("commander.approval.denied", "info") == "error"
    assert classify_severity("capability_registry.selection.unavailable", "info") == "error"


def test_retry_and_degraded_events_classify_as_warn():
    assert classify_severity("workflow_engine.step.retry_scheduled", "info") == "warn"
    assert classify_severity("supervisor.unit.unhealthy", "info") == "warn"
    assert classify_severity("task_queue.task.recovered", "info") == "warn"


def test_ordinary_events_stay_at_the_original_level():
    assert classify_severity("commander.request.received", "info") == "info"
    assert classify_severity("mission_system.mission.created", "info") == "info"


def test_an_explicitly_elevated_level_is_never_downgraded():
    """If a module ever DOES start setting a real level, that's honored
    over the keyword inference, not overridden by it."""
    assert classify_severity("commander.request.received", "error") == "error"
    assert classify_severity("commander.request.received", "warn") == "warn"
