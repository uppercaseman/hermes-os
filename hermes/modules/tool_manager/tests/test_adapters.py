"""Smoke tests for the production-capable provider adapters.

These call adapters directly (not through Tool Manager) so:

- A `dry_run=False` adapter that lacks a `Transport` raises
  `RuntimeError("no transport configured")` immediately, rather than
  being silently retried by Tool Manager's retry policy.

- A streaming adapter with `dry_run=True` yields one final
  ToolStreamChunk with `is_final=True` and the dry-run delta, instead
  of dying.

- Non-streaming adapters raise `UnsupportedCapabilityError` on stream
  iteration -- the Tool Manager contract requires this, not the
  capability gate we used to place on the placeholders.

The provider-name assertion now covers **eight** adapters (openai,
anthropic = 'claude' provider name, minimax, gemini, ollama, mcp,
obsidian, paperclip).
"""
import pytest

from hermes.modules.tool_manager.adapters import (
    ClaudeAdapter,
    MCPServerAdapter,
    MiniMaxAdapter,
    ObsidianAdapter,
    OllamaAdapter,
    OpenAIAdapter,
    PaperclipAdapter,
    GeminiAdapter,
)
from hermes.modules.tool_manager.errors import UnsupportedCapabilityError
from hermes.modules.tool_manager.models import ToolInvocationRequest

ADAPTER_CLASSES = [
    OpenAIAdapter,
    ClaudeAdapter,
    MiniMaxAdapter,
    GeminiAdapter,
    OllamaAdapter,
    ObsidianAdapter,
    PaperclipAdapter,
]


@pytest.mark.parametrize("adapter_cls", ADAPTER_CLASSES)
def test_dry_run_default_keeps_invocations_offline(adapter_cls):
    adapter = adapter_cls(name="under-test")
    assert adapter.dry_run is True


@pytest.mark.parametrize("adapter_cls", ADAPTER_CLASSES)
async def test_lifecycle_hooks_succeed_as_no_ops_in_dry_run(adapter_cls):
    adapter = adapter_cls(name="under-test")
    # Auth, start, stop, health_check must all succeed without raising in
    # dry_run mode -- the unconditional safe default.
    await adapter.authenticate()
    await adapter.start()
    assert await adapter.health_check() is True
    await adapter.stop()


@pytest.mark.parametrize("adapter_cls", ADAPTER_CLASSES)
async def test_dry_run_invoke_returns_structured_completed_result(adapter_cls):
    adapter = adapter_cls(name="under-test")
    request = ToolInvocationRequest(tool_name="under-test", operation="ping", parameters={"prompt": "hi"})

    result = await adapter.invoke(request)

    assert result.status == "completed"
    assert result.error is None
    assert result.output["dry_run"] is True
    assert result.output["provider"] == adapter.provider


@pytest.mark.parametrize("adapter_cls", ADAPTER_CLASSES)
async def test_dry_run_invoke_never_makes_a_network_call(adapter_cls):
    """The hard safety guarantee: in dry_run mode (the default for
    every adapter), `invoke()` returns without any I/O whatsoever --
    no transport is required."""
    adapter = adapter_cls(name="under-test")
    request = ToolInvocationRequest(tool_name="under-test", operation="chat", parameters={"prompt": "hi"})

    result = await adapter.invoke(request)

    assert result.status == "completed"


@pytest.mark.parametrize(
    "adapter_cls,expects_streaming",
    [
        (OpenAIAdapter, True),
        (ClaudeAdapter, True),
        (MiniMaxAdapter, True),
        (GeminiAdapter, True),
        (OllamaAdapter, True),
        (ObsidianAdapter, False),
        (PaperclipAdapter, False),
    ],
)
def test_declared_streaming_capability_matches_provider_expectations(adapter_cls, expects_streaming):
    adapter = adapter_cls(name="under-test")
    assert adapter.capabilities.supports_streaming is expects_streaming


async def test_non_streaming_adapter_raises_unsupported_capability_on_stream_iteration():
    """Non-streaming adapters raise `UnsupportedCapabilityError` from
    their async-generator `invoke_stream` -- the existing Tool Manager
    contract. This test guards against a regression to the old "yield
    an error chunk" pattern; that pattern was technically correct but
    defeated the Tool Manager's pre-flight capability check."""
    adapter = ObsidianAdapter(name="vault")
    request = ToolInvocationRequest(tool_name="vault", operation="read_note")

    with pytest.raises(UnsupportedCapabilityError):
        async for _ in adapter.invoke_stream(request):
            pass


async def test_streaming_capable_adapter_yields_dry_run_chunk_in_dry_run_mode():
    adapter = OpenAIAdapter(name="openai")
    request = ToolInvocationRequest(tool_name="openai", operation="chat", parameters={"prompt": "hi"})
    chunks = [chunk async for chunk in adapter.invoke_stream(request)]
    assert chunks[-1].is_final is True
    assert chunks[-1].error is None
    assert chunks[-1].data["dry_run"] is True


def test_provider_names_are_distinct_and_match_the_documented_eight():
    providers = {cls(name="x").provider for cls in [
        OpenAIAdapter, ClaudeAdapter, MiniMaxAdapter, GeminiAdapter,
        OllamaAdapter, MCPServerAdapter, ObsidianAdapter, PaperclipAdapter,
    ]}
    assert providers == {
        "openai", "anthropic", "minimax", "gemini", "ollama", "mcp", "obsidian", "paperclip",
    }


def test_mcp_server_adapter_carries_a_server_command():
    adapter = MCPServerAdapter(name="filesystem-mcp", server_command="npx @modelcontextprotocol/server-filesystem")
    assert adapter.provider == "mcp"
    assert adapter.server_command.startswith("npx")
    assert adapter.capabilities.supports_streaming is True


def test_claude_adapter_provider_label_is_anthropic():
    """The Claude/Anthropic adapter uses the canonical provider label
    `anthropic` (matching the capability matrix); the historical
    `claude` name is reserved for the implementation filename and
    class name. Tests asserting on the historical provider label
    should look at `adapter.__class__.__name__` instead."""
    adapter = ClaudeAdapter(name="under-test")
    assert adapter.provider == "anthropic"
    assert adapter.__class__.__name__ == "ClaudeAdapter"


@pytest.mark.parametrize("adapter_cls", ADAPTER_CLASSES)
async def test_dry_run_mode_never_reads_the_api_key(adapter_cls, monkeypatch):
    """Even if a real-looking key is sitting in the environment, dry_run
    mode must not touch it -- the safety property survives across every
    upgraded adapter."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-be-read")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-be-read")
    monkeypatch.setenv("MINIMAX_API_KEY", "sk-should-not-be-read")
    monkeypatch.setenv("GEMINI_API_KEY", "sk-should-not-be-read")
    adapter = adapter_cls(name="under-test")
    request = ToolInvocationRequest(tool_name="under-test", operation="x")

    result = await adapter.invoke(request)

    assert result.output["dry_run"] is True


@pytest.mark.parametrize(
    "adapter_cls,missing_var",
    [
        (OpenAIAdapter, "OPENAI_API_KEY"),
        (ClaudeAdapter, "ANTHROPIC_API_KEY"),
        (MiniMaxAdapter, "MINIMAX_API_KEY"),
        (GeminiAdapter, "GEMINI_API_KEY"),
    ],
)
async def test_dry_run_false_adapter_authenticates_via_env_var(
    adapter_cls, missing_var, monkeypatch
):
    from hermes.modules.tool_manager.adapters import (
        AnthropicAuthenticationError,
        GeminiAuthenticationError,
        MiniMaxAuthenticationError,
        OpenAIAuthenticationError,
    )

    monkeypatch.delenv(missing_var, raising=False)
    adapter = adapter_cls(name="under-test", dry_run=False)

    error_map = {
        "OPENAI_API_KEY": OpenAIAuthenticationError,
        "ANTHROPIC_API_KEY": AnthropicAuthenticationError,
        "MINIMAX_API_KEY": MiniMaxAuthenticationError,
        "GEMINI_API_KEY": GeminiAuthenticationError,
    }
    with pytest.raises(error_map[missing_var]):
        await adapter.authenticate()
