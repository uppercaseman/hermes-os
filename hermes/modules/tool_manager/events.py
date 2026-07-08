"""Event-type constants the Tool Manager publishes.

Namespaced `tool_manager.*`, following the OS-wide `domain.entity.action`
convention. Adapter lifecycle events (starting/started/crashed/restarting/
stopped) are NOT duplicated here -- they come from the Supervisor that
actually manages each adapter (`supervisor.unit.*`); Tool Manager only
publishes events about invocations, which is the part it alone owns.
"""

TOOL_INVOKED = "tool_manager.tool.invoked"
TOOL_INVOCATION_FAILED = "tool_manager.tool.invocation_failed"
TOOL_RETRY_SCHEDULED = "tool_manager.tool.retry_scheduled"
TOOL_STREAM_FAILED = "tool_manager.tool.stream_failed"
