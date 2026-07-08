import time

import pytest

from hermes.modules.tool_manager.errors import UnknownHandleError, UnsupportedCapabilityError
from hermes.modules.tool_manager.events import TOOL_RETRY_SCHEDULED
from hermes.modules.tool_manager.interface import build_tool_manager
from hermes.modules.tool_manager.models import (
    RateLimitPolicy,
    ToolCapabilities,
    ToolInvocationRequest,
)
from hermes.modules.tool_manager.tests.conftest import fast_config
from hermes.modules.tool_manager.tests.fakes import ScriptedToolAdapter


def _request(tool_name: str, operation: str = "do_thing") -> ToolInvocationRequest:
    return ToolInvocationRequest(tool_name=tool_name, operation=operation)


async def test_registering_duplicate_name_raises(tool_manager):
    tool_manager.register_adapter(ScriptedToolAdapter(name="dup"), fast_config("dup"))

    with pytest.raises(ValueError):
        tool_manager.register_adapter(ScriptedToolAdapter(name="dup"), fast_config("dup"))


async def test_invoking_unknown_tool_raises_key_error(tool_manager):
    with pytest.raises(KeyError):
        await tool_manager.invoke(_request("nope"))


async def test_start_all_starts_every_adapter_via_the_supervisor(tool_manager):
    adapter = ScriptedToolAdapter(name="a")
    tool_manager.register_adapter(adapter, fast_config("a"))

    await tool_manager.start_all()

    assert adapter.authenticate_calls == 1
    assert adapter.start_calls == 1
    status = await tool_manager.status("a")
    assert status.state == "running"

    await tool_manager.stop_all()
    assert adapter.stop_calls == 1


async def test_invoke_happy_path_returns_completed_result(tool_manager):
    adapter = ScriptedToolAdapter(name="a")
    tool_manager.register_adapter(adapter, fast_config("a"))

    result = await tool_manager.invoke(_request("a", operation="ping"))

    assert result.status == "completed"
    assert result.output == {"echo": "ping"}
    assert result.attempts == 1


async def test_invoke_retries_then_succeeds(tool_manager, bus):
    adapter = ScriptedToolAdapter(name="a", invoke_outcomes=["raise", "ok"])
    tool_manager.register_adapter(adapter, fast_config("a"))

    retries = []

    async def capture(event):
        retries.append(event)

    await bus.subscribe(TOOL_RETRY_SCHEDULED, capture)

    result = await tool_manager.invoke(_request("a"))

    assert result.status == "completed"
    assert result.attempts == 2
    assert len(retries) == 1


async def test_invoke_exhausts_retries_and_returns_failed_without_raising(tool_manager):
    adapter = ScriptedToolAdapter(name="a", invoke_outcomes=["raise", "raise", "raise"])
    tool_manager.register_adapter(adapter, fast_config("a"))

    result = await tool_manager.invoke(_request("a"))

    assert result.status == "failed"
    assert "scripted invocation failure" in result.error
    assert result.attempts == 3


async def test_status_all_reports_every_registered_tool(tool_manager):
    tool_manager.register_adapter(ScriptedToolAdapter(name="a", provider="fake-a"), fast_config("a"))
    tool_manager.register_adapter(ScriptedToolAdapter(name="b", provider="fake-b"), fast_config("b"))
    await tool_manager.start_all()

    statuses = await tool_manager.status_all()

    assert {s.name for s in statuses} == {"a", "b"}
    assert {s.provider for s in statuses} == {"fake-a", "fake-b"}

    await tool_manager.stop_all()


async def test_status_tracks_invocation_and_failure_counts(tool_manager):
    adapter = ScriptedToolAdapter(name="a", invoke_outcomes=["ok", "raise", "raise", "raise"])
    tool_manager.register_adapter(adapter, fast_config("a"))

    await tool_manager.invoke(_request("a"))  # succeeds
    await tool_manager.invoke(_request("a"))  # exhausts retries -> failed

    status = await tool_manager.status("a")
    assert status.total_invocations == 4  # 1 success + 3 attempts of the failure
    assert status.total_failures == 3


# --------------------------------------------------------------------- #
# Asynchronous execution
# --------------------------------------------------------------------- #

async def test_invoke_async_returns_handle_immediately_and_get_result_polls(tool_manager):
    adapter = ScriptedToolAdapter(name="a")
    tool_manager.register_adapter(adapter, fast_config("a"))

    handle = await tool_manager.invoke_async(_request("a"))
    result = await tool_manager.get_result(handle)
    while result is None:
        result = await tool_manager.get_result(handle)

    assert result.status == "completed"


async def test_get_result_raises_for_unknown_handle(tool_manager):
    adapter = ScriptedToolAdapter(name="a")
    tool_manager.register_adapter(adapter, fast_config("a"))
    handle = await tool_manager.invoke_async(_request("a"))
    result = None
    while result is None:
        result = await tool_manager.get_result(handle)

    with pytest.raises(UnknownHandleError):
        await tool_manager.get_result(handle)  # already consumed


async def test_await_result_blocks_until_the_invocation_completes(tool_manager):
    adapter = ScriptedToolAdapter(name="a")
    tool_manager.register_adapter(adapter, fast_config("a"))

    handle = await tool_manager.invoke_async(_request("a"))
    result = await tool_manager.await_result(handle, timeout=5.0)

    assert result.status == "completed"


# --------------------------------------------------------------------- #
# Streaming execution
# --------------------------------------------------------------------- #

async def test_invoke_stream_yields_all_chunks_in_order(tool_manager):
    adapter = ScriptedToolAdapter(
        name="a",
        capabilities=ToolCapabilities(supports_streaming=True),
        stream_chunks=[{"i": 0}, {"i": 1}, {"i": 2}],
    )
    tool_manager.register_adapter(adapter, fast_config("a"))

    chunks = [chunk async for chunk in tool_manager.invoke_stream(_request("a"))]

    assert [c.data["i"] for c in chunks] == [0, 1, 2]
    assert chunks[-1].is_final is True
    assert all(c.error is None for c in chunks)


async def test_invoke_stream_on_non_streaming_adapter_raises(tool_manager):
    adapter = ScriptedToolAdapter(name="a", capabilities=ToolCapabilities(supports_streaming=False))
    tool_manager.register_adapter(adapter, fast_config("a"))

    with pytest.raises(UnsupportedCapabilityError):
        async for _ in tool_manager.invoke_stream(_request("a")):
            pass


async def test_invoke_stream_retries_setup_then_yields_error_chunk_after_exhaustion(tool_manager):
    adapter = ScriptedToolAdapter(
        name="a",
        capabilities=ToolCapabilities(supports_streaming=True),
        stream_setup_outcomes=["raise", "raise", "raise"],
    )
    tool_manager.register_adapter(adapter, fast_config("a"))

    chunks = [chunk async for chunk in tool_manager.invoke_stream(_request("a"))]

    assert len(chunks) == 1
    assert chunks[0].is_final is True
    assert "scripted stream setup failure" in chunks[0].error


async def test_invoke_stream_retries_setup_then_succeeds(tool_manager):
    adapter = ScriptedToolAdapter(
        name="a",
        capabilities=ToolCapabilities(supports_streaming=True),
        stream_setup_outcomes=["raise", "ok"],
        stream_chunks=[{"i": 0}],
    )
    tool_manager.register_adapter(adapter, fast_config("a"))

    chunks = [chunk async for chunk in tool_manager.invoke_stream(_request("a"))]

    assert len(chunks) == 1
    assert chunks[0].error is None
    assert chunks[0].data == {"i": 0}


# --------------------------------------------------------------------- #
# Provider independence / swappability
# --------------------------------------------------------------------- #

async def test_swapping_the_adapter_behind_a_tool_name_requires_no_caller_changes(tool_manager, bus):
    """The whole point of the adapter architecture: a caller only ever
    knows the logical tool name, never which provider backs it."""
    tool_manager.register_adapter(
        ScriptedToolAdapter(name="primary_llm", provider="openai"), fast_config("primary_llm")
    )
    result_a = await tool_manager.invoke(_request("primary_llm", operation="chat"))

    other = build_tool_manager(event_bus=bus)
    other.register_adapter(
        ScriptedToolAdapter(name="primary_llm", provider="claude"), fast_config("primary_llm")
    )
    result_b = await other.invoke(_request("primary_llm", operation="chat"))

    assert result_a.status == result_b.status == "completed"
    assert result_a.output == result_b.output  # identical caller-visible contract


# --------------------------------------------------------------------- #
# Rate limiting integration
# --------------------------------------------------------------------- #

async def test_invoke_is_rate_limited_per_adapter(tool_manager):
    adapter = ScriptedToolAdapter(name="a")
    tool_manager.register_adapter(
        adapter, fast_config("a", rate_limit=RateLimitPolicy(max_calls=1, per_seconds=0.05))
    )

    start = time.monotonic()
    await tool_manager.invoke(_request("a"))
    await tool_manager.invoke(_request("a"))  # bucket now empty -- must wait for a refill
    elapsed = time.monotonic() - start

    assert elapsed > 0.01
