"""Mock research tool adapter.

Unlike Tool Manager's OpenAI/Claude/MiniMax/Obsidian/Paperclip/MCP
adapters -- which are placeholders for real integrations that don't
exist YET -- this one is deliberately and permanently fake. There is no
real "research tool" it stands in for; it exists so the Research Brief
workflow can run end to end without touching any live external API,
returning canned-but-structured content instead of raising
NotImplementedError.
"""
from __future__ import annotations

from hermes.modules.tool_manager.adapters.base import BaseToolAdapter
from hermes.modules.tool_manager.models import ToolCapabilities, ToolInvocationRequest, ToolInvocationResult


class MockResearchAdapter(BaseToolAdapter):
    provider = "mock_research"
    capabilities = ToolCapabilities(supports_sync=True, supports_streaming=False, requires_auth=False)

    async def invoke(self, request: ToolInvocationRequest) -> ToolInvocationResult:
        topic = request.parameters.get("topic") or "the requested topic"
        return ToolInvocationResult(
            tool_name=request.tool_name,
            correlation_id=request.correlation_id,
            status="completed",
            output={
                "topic": topic,
                "summary": (
                    f"Mock research summary for '{topic}': this is placeholder content "
                    f"standing in for a real research-tool integration."
                ),
                "sources": [
                    f"https://example.invalid/mock-source-1?topic={topic}",
                    f"https://example.invalid/mock-source-2?topic={topic}",
                ],
            },
        )
