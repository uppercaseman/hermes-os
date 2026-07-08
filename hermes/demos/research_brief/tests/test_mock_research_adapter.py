from hermes.demos.research_brief.mock_research_adapter import MockResearchAdapter
from hermes.modules.tool_manager.models import ToolInvocationRequest


async def test_invoke_returns_completed_with_the_topic_in_the_summary():
    adapter = MockResearchAdapter(name="mock_research")
    request = ToolInvocationRequest(tool_name="mock_research", operation="research", parameters={"topic": "hermes os"})

    result = await adapter.invoke(request)

    assert result.status == "completed"
    assert "hermes os" in result.output["summary"]
    assert len(result.output["sources"]) == 2


async def test_invoke_handles_a_missing_topic_gracefully():
    adapter = MockResearchAdapter(name="mock_research")
    request = ToolInvocationRequest(tool_name="mock_research", operation="research", parameters={})

    result = await adapter.invoke(request)

    assert result.status == "completed"
    assert "the requested topic" in result.output["summary"]


def test_declares_no_streaming_and_no_auth_required():
    adapter = MockResearchAdapter(name="mock_research")

    assert adapter.capabilities.supports_streaming is False
    assert adapter.capabilities.requires_auth is False
    assert adapter.provider == "mock_research"
