from datetime import datetime, timezone

from hermes.core.state_manager.models import Heartbeat, RestartRequest, SystemDiagnostics


def test_heartbeat_defaults_have_no_detail():
    heartbeat = Heartbeat(module_name="tool_manager", state="healthy")

    assert heartbeat.detail is None
    assert heartbeat.reported_at is not None


def test_restart_request_defaults_to_pending():
    request = RestartRequest(module_name="tool_manager")

    assert request.status == "pending"
    assert request.requested_by == "unknown"


def test_system_diagnostics_holds_a_module_list():
    diagnostics = SystemDiagnostics(generated_at=datetime.now(timezone.utc), modules=[], overall_state="healthy")

    assert diagnostics.modules == []
    assert diagnostics.overall_state == "healthy"
