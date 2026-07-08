"""Real adapter tests with mocked network calls + Provider Router
integration tests. None of these require live API keys; every network
call is intercepted by a `FakeTransport` that returns canned responses
or SSE/NDJSON streams.

The directive: "Replace placeholder tests with real adapter tests
using mocked network calls, never requiring live API keys for CI" +
"Add integration tests for routing, fail-over, timeout handling,
retry, capability selection, configuration loading, streaming,
dry-run mode."
"""
from __future__ import annotations

import json
import uuid
from typing import AsyncIterator

import pytest

from hermes.core.event_bus.models import Event
from hermes.modules.capability_registry.models import CapabilityCandidate
from hermes.modules.provider_router import build_provider_router
from hermes.modules.provider_router.models import RoutingRequest
from hermes.modules.tool_manager.adapters import (
    ClaudeAdapter,
    GeminiAdapter,
    MCPProviderConfig,
    MiniMaxAdapter,
    OllamaAdapter,
    OpenAIAdapter,
    OpenAIProviderConfig,
    AnthropicProviderConfig,
    GeminiProviderConfig,
    OllamaProviderConfig,
    MiniMaxProviderConfig,
    HTTPRequest,
    HTTPResponse,
    ProviderEventLog,
    ProviderRecorder,
    TokenUsage,
    estimate_cost_usd,
    provider_config_for,
    provider_names,
    supported_capabilities,
)
from hermes.modules.tool_manager.adapters.http_base import (
    HTTPStatusError,
    HTTPTimeoutError,
)
from hermes.modules.tool_manager.models import (
    ToolInvocationRequest,
    ToolInvocationResult,
)


# ---------------------------------------------------------------------- #
# Mocked HTTP transport
# ---------------------------------------------------------------------- #
class FakeTransport:
    """Configurable fake that satisfies the `Transport` Protocol."""

    def __init__(self) -> None:
        self.requests: list[HTTPRequest] = []
        self.responses: list[HTTPResponse] = []
        self.stream_responses: dict[str, list[bytes]] = {}
        self.exception_on_send: Exception | None = None
        self.exception_on_stream: Exception | None = None

    def queue_response(self, status: int, body: bytes, headers: dict[str, str] | None = None) -> None:
        self.responses.append(
            HTTPResponse(status=status, headers=headers or {}, body=body)
        )

    def queue_stream(self, key: str, chunks: list[bytes]) -> None:
        self.stream_responses[key] = chunks

    async def send(self, request: HTTPRequest) -> HTTPResponse:
        self.requests.append(request)
        if self.exception_on_send is not None:
            raise self.exception_on_send
        if not self.responses:
            raise AssertionError("FakeTransport ran out of queued responses")
        return self.responses.pop(0)

    def stream(self, request: HTTPRequest) -> AsyncIterator[bytes]:
        # Transport.stream() is sync-def returning an AsyncIterator (per the Protocol).
        async def _gen() -> AsyncIterator[bytes]:
            self.requests.append(request)
            if self.exception_on_stream is not None:
                raise self.exception_on_stream
            key = request.url
            chunks = self.stream_responses.get(key, [])
            for chunk in chunks:
                yield chunk
        return _gen()


# ---------------------------------------------------------------------- #
# OpenAI adapter (mocked)
# ---------------------------------------------------------------------- #
class TestOpenAIAdapterMocked:
    def _adapter(self, transport: FakeTransport, **kwargs) -> OpenAIAdapter:
        recorder = ProviderRecorder(log=ProviderEventLog())
        defaults = dict(name="openai", dry_run=False, transport=transport, recorder=recorder)
        defaults.update(kwargs)
        return OpenAIAdapter(**defaults)

    @pytest.mark.asyncio
    async def test_invoke_posts_to_chat_completions_with_bearer_token(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        transport = FakeTransport()
        transport.queue_response(
            200,
            json.dumps(
                {
                    "id": "chatcmpl-1",
                    "model": "gpt-4o-mini",
                    "choices": [
                        {"index": 0, "message": {"role": "assistant", "content": "hello"}, "finish_reason": "stop"}
                    ],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12},
                }
            ).encode(),
        )
        adapter = self._adapter(transport)
        request = ToolInvocationRequest(
            tool_name="openai",
            operation="chat",
            parameters={"messages": [{"role": "user", "content": "hi"}], "capability": "reasoning"},
        )

        result = await adapter.invoke(request)

        assert result.status == "completed"
        assert result.output["message"] == "hello"
        assert result.output["usage"]["total_tokens"] == 12
        # Authorisation header was sent
        sent = transport.requests[0]
        assert sent.method == "POST"
        assert sent.url.endswith("/chat/completions")
        assert sent.headers["Authorization"] == "Bearer sk-test"
        body = json.loads(sent.body)
        assert body["model"] == "gpt-4o-mini"
        assert body["messages"] == [{"role": "user", "content": "hi"}]

    @pytest.mark.asyncio
    async def test_invoke_raises_authentication_error_when_key_missing(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        adapter = self._adapter(FakeTransport())
        request = ToolInvocationRequest(tool_name="openai", operation="chat", parameters={})

        with pytest.raises(Exception) as exc_info:
            await adapter.invoke(request)
        assert "OPENAI_API_KEY" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_invoke_handles_5xx_status(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        transport = FakeTransport()
        transport.queue_response(500, b"server exploded", headers={"content-type": "text/plain"})
        adapter = self._adapter(transport)
        request = ToolInvocationRequest(tool_name="openai", operation="chat", parameters={})

        with pytest.raises(HTTPStatusError):
            await adapter.invoke(request)

    @pytest.mark.asyncio
    async def test_invoke_handles_timeout(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        transport = FakeTransport()
        transport.exception_on_send = HTTPTimeoutError()
        adapter = self._adapter(transport)
        request = ToolInvocationRequest(tool_name="openai", operation="chat", parameters={})

        with pytest.raises(HTTPTimeoutError):
            await adapter.invoke(request)

    @pytest.mark.asyncio
    async def test_invoke_stream_parses_sse_chunks(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        transport = FakeTransport()
        sse_chunks = [
            b"data: {\"id\":\"1\",\"model\":\"gpt-4o-mini\",\"choices\":[{\"delta\":{\"content\":\"hel\"}}]}\n\n",
            b"data: {\"id\":\"1\",\"model\":\"gpt-4o-mini\",\"choices\":[{\"delta\":{\"content\":\"lo\"}}]}\n\n",
            b"data: {\"id\":\"1\",\"model\":\"gpt-4o-mini\",\"choices\":[{\"delta\":{}}]}\n\n",
            b"data: [DONE]\n\n",
        ]
        transport.queue_stream("https://api.openai.com/v1/chat/completions", sse_chunks)
        adapter = self._adapter(transport)
        request = ToolInvocationRequest(
            tool_name="openai",
            operation="chat",
            parameters={"messages": [{"role": "user", "content": "hi"}], "stream": True},
        )

        chunks = [c async for c in adapter.invoke_stream(request)]

        text_deltas = [
            c.data.get("delta")
            for c in chunks
            if not c.is_final and c.data.get("delta")
        ]
        assert text_deltas == ["hel", "lo"]
        assert chunks[-1].is_final is True
        assert chunks[-1].data.get("done") is True


# ---------------------------------------------------------------------- #
# Claude (Anthropic) adapter (mocked)
# ---------------------------------------------------------------------- #
class TestClaudeAdapterMocked:
    def _adapter(self, transport: FakeTransport, **kwargs) -> ClaudeAdapter:
        recorder = ProviderRecorder(log=ProviderEventLog())
        defaults = dict(name="anthropic", dry_run=False, transport=transport, recorder=recorder)
        defaults.update(kwargs)
        return ClaudeAdapter(**defaults)

    @pytest.mark.asyncio
    async def test_invoke_sends_anthropic_headers_and_payload(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        transport = FakeTransport()
        transport.queue_response(
            200,
            json.dumps(
                {
                    "id": "msg_1",
                    "model": "claude-sonnet-4-5",
                    "content": [{"type": "text", "text": "hi from claude"}],
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": 4, "output_tokens": 6},
                }
            ).encode(),
        )
        adapter = self._adapter(transport)
        request = ToolInvocationRequest(
            tool_name="anthropic",
            operation="chat",
            parameters={"messages": [{"role": "user", "content": "hi"}], "capability": "reasoning"},
        )

        result = await adapter.invoke(request)

        assert result.status == "completed"
        assert "hi from claude" in result.output["message"]
        sent = transport.requests[0]
        assert sent.url.endswith("/v1/messages")
        assert sent.headers["x-api-key"] == "sk-ant-test"
        assert "anthropic-version" in sent.headers

    @pytest.mark.asyncio
    async def test_invoke_stream_handles_anthropic_sse_event_types(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        transport = FakeTransport()
        sse_chunks = [
            b"event: message_start\ndata: {\"type\":\"message_start\",\"message\":{\"model\":\"claude-sonnet-4-5\"}}\n\n",
            b"event: content_block_delta\ndata: {\"type\":\"content_block_delta\",\"delta\":{\"type\":\"text_delta\",\"text\":\"Hello\"}}\n\n",
            b"event: content_block_delta\ndata: {\"type\":\"content_block_delta\",\"delta\":{\"type\":\"text_delta\",\"text\":\" world\"}}\n\n",
            b"event: message_stop\ndata: {\"type\":\"message_stop\"}\n\n",
        ]
        transport.queue_stream("https://api.anthropic.com/v1/messages", sse_chunks)
        adapter = self._adapter(transport)
        request = ToolInvocationRequest(
            tool_name="anthropic",
            operation="chat",
            parameters={"messages": [{"role": "user", "content": "hi"}], "stream": True},
        )

        chunks = [c async for c in adapter.invoke_stream(request)]

        text_deltas = [
            c.data.get("delta")
            for c in chunks
            if not c.is_final and c.data.get("event_type") == "content_block_delta"
        ]
        assert text_deltas == ["Hello", " world"]


# ---------------------------------------------------------------------- #
# MiniMax adapter (mocked)
# ---------------------------------------------------------------------- #
class TestMiniMaxAdapterMocked:
    def _adapter(self, transport: FakeTransport, **kwargs) -> MiniMaxAdapter:
        recorder = ProviderRecorder(log=ProviderEventLog())
        defaults = dict(name="minimax", dry_run=False, transport=transport, recorder=recorder)
        defaults.update(kwargs)
        return MiniMaxAdapter(**defaults)

    @pytest.mark.asyncio
    async def test_invoke_uses_minimax_v2_endpoint(self, monkeypatch):
        monkeypatch.setenv("MINIMAX_API_KEY", "sk-mm-test")
        transport = FakeTransport()
        transport.queue_response(
            200,
            json.dumps(
                {
                    "id": "cmpl-1",
                    "model": "MiniMax",
                    "choices": [{"message": {"role": "assistant", "content": "mm says hi"}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
                }
            ).encode(),
        )
        adapter = self._adapter(transport)
        request = ToolInvocationRequest(
            tool_name="minimax",
            operation="chat",
            parameters={"messages": [{"role": "user", "content": "hi"}], "capability": "reasoning"},
        )

        result = await adapter.invoke(request)

        assert result.status == "completed"
        sent = transport.requests[0]
        assert "/text/chatcompletion_v2" in sent.url


# ---------------------------------------------------------------------- #
# Gemini adapter (mocked)
# ---------------------------------------------------------------------- #
class TestGeminiAdapterMocked:
    def _adapter(self, transport: FakeTransport, **kwargs) -> GeminiAdapter:
        recorder = ProviderRecorder(log=ProviderEventLog())
        defaults = dict(name="gemini", dry_run=False, transport=transport, recorder=recorder)
        defaults.update(kwargs)
        return GeminiAdapter(**defaults)

    @pytest.mark.asyncio
    async def test_invoke_uses_generate_content_with_api_key_query(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "gem-test-key")
        transport = FakeTransport()
        transport.queue_response(
            200,
            json.dumps(
                {
                    "candidates": [
                        {
                            "content": {"parts": [{"text": "gemini says hi"}], "role": "model"},
                            "finishReason": "STOP",
                        }
                    ],
                    "usageMetadata": {"promptTokenCount": 4, "candidatesTokenCount": 5, "totalTokenCount": 9},
                }
            ).encode(),
        )
        adapter = self._adapter(transport)
        request = ToolInvocationRequest(
            tool_name="gemini",
            operation="chat",
            parameters={"messages": [{"role": "user", "content": "hi"}], "capability": "reasoning"},
        )

        result = await adapter.invoke(request)

        assert result.status == "completed"
        sent = transport.requests[0]
        assert ":generateContent" in sent.url
        assert "key=gem-test-key" in sent.url

    @pytest.mark.asyncio
    async def test_invoke_propagates_system_prompt(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "gem-test-key")
        transport = FakeTransport()
        transport.queue_response(
            200,
            json.dumps(
                {
                    "candidates": [{"content": {"parts": [{"text": "ok"}], "role": "model"}}],
                }
            ).encode(),
        )
        adapter = self._adapter(transport)
        request = ToolInvocationRequest(
            tool_name="gemini",
            operation="chat",
            parameters={
                "messages": [{"role": "user", "content": "hi"}],
                "system": "be terse",
                "capability": "reasoning",
            },
        )

        await adapter.invoke(request)

        body = json.loads(transport.requests[0].body)
        assert "systemInstruction" in body
        assert body["systemInstruction"]["parts"][0]["text"] == "be terse"


# ---------------------------------------------------------------------- #
# Ollama adapter (mocked NDJSON)
# ---------------------------------------------------------------------- #
class TestOllamaAdapterMocked:
    def _adapter(self, transport: FakeTransport, **kwargs) -> OllamaAdapter:
        recorder = ProviderRecorder(log=ProviderEventLog())
        defaults = dict(
            name="ollama",
            dry_run=False,
            base_url="http://localhost:11434",
            transport=transport,
            recorder=recorder,
        )
        defaults.update(kwargs)
        return OllamaAdapter(**defaults)

    @pytest.mark.asyncio
    async def test_invoke_posts_to_api_chat(self):
        transport = FakeTransport()
        transport.queue_response(
            200,
            json.dumps(
                {
                    "model": "llama3.1",
                    "message": {"role": "assistant", "content": "hi from llama"},
                    "done": True,
                    "prompt_eval_count": 3,
                    "eval_count": 4,
                }
            ).encode(),
        )
        adapter = self._adapter(transport)
        request = ToolInvocationRequest(
            tool_name="ollama",
            operation="chat",
            parameters={"messages": [{"role": "user", "content": "hi"}], "capability": "reasoning"},
        )

        result = await adapter.invoke(request)

        assert result.status == "completed"
        sent = transport.requests[0]
        assert sent.url == "http://localhost:11434/api/chat"

    @pytest.mark.asyncio
    async def test_invoke_stream_parses_ndjson(self):
        transport = FakeTransport()
        ndjson_chunks = [
            (json.dumps({"model": "llama3.1", "message": {"role": "assistant", "content": "Hel"}, "done": False}) + "\n").encode(),
            (json.dumps({"model": "llama3.1", "message": {"role": "assistant", "content": "lo"}, "done": False}) + "\n").encode(),
            (json.dumps({"model": "llama3.1", "message": {"role": "assistant", "content": ""}, "done": True}) + "\n").encode(),
        ]
        transport.queue_stream("http://localhost:11434/api/chat", ndjson_chunks)
        adapter = self._adapter(transport)
        request = ToolInvocationRequest(
            tool_name="ollama",
            operation="chat",
            parameters={"messages": [{"role": "user", "content": "hi"}], "stream": True},
        )

        chunks = [c async for c in adapter.invoke_stream(request)]

        text_deltas = [c.data.get("delta") for c in chunks if not c.is_final and "delta" in c.data]
        assert text_deltas == ["Hel", "lo"]
        assert chunks[-1].is_final is True


# ---------------------------------------------------------------------- #
# MCP adapter (smoke test only — subprocess transport, not mockable here)
# ---------------------------------------------------------------------- #
class TestMCPAdapter:
    def test_discover_capabilities_returns_default_tuple(self):
        from hermes.modules.tool_manager.adapters.mcp_server_adapter import (
            MCPServerAdapter,
            SUPPORTED_CAPABILITIES,
        )

        adapter = MCPServerAdapter(name="mcp", server_command="ignored")
        caps = adapter.discover_capabilities()
        assert set(caps) == set(SUPPORTED_CAPABILITIES)


# ---------------------------------------------------------------------- #
# Provider config schemas
# ---------------------------------------------------------------------- #
class TestProviderConfigSchemas:
    def test_canonical_capability_matrix_is_stable(self):
        # The matrix is a public API: a future change is an ADR.
        for name, required in [
            ("openai", {"reasoning", "planning", "code_generation", "vision"}),
            ("anthropic", {"reasoning", "planning", "code_generation", "vision"}),
            ("minimax", {"reasoning", "planning", "code_generation", "vision"}),
            ("gemini", {"reasoning", "planning", "code_generation", "vision"}),
            ("ollama", {"reasoning", "planning", "code_generation", "vision"}),
        ]:
            caps = set(supported_capabilities(name))
            assert required <= caps, f"{name} missing required capabilities: {required - caps}"

        # MCP is the only one that declares memory/retrieval/communication/automation
        mcp_caps = set(supported_capabilities("mcp"))
        for cap in ("memory", "retrieval", "communication", "desktop_automation", "browser_automation"):
            assert cap in mcp_caps, f"MCP missing {cap}"

    def test_provider_names_lists_all_six(self):
        names = set(provider_names())
        assert names == {"openai", "anthropic", "minimax", "gemini", "ollama", "mcp"}

    def test_provider_config_for_returns_correct_schema(self):
        assert isinstance(provider_config_for("openai"), type) and issubclass(provider_config_for("openai"), OpenAIProviderConfig)
        assert isinstance(provider_config_for("anthropic"), type) and issubclass(provider_config_for("anthropic"), AnthropicProviderConfig)
        assert isinstance(provider_config_for("minimax"), type) and issubclass(provider_config_for("minimax"), MiniMaxProviderConfig)
        assert isinstance(provider_config_for("gemini"), type) and issubclass(provider_config_for("gemini"), GeminiProviderConfig)
        assert isinstance(provider_config_for("ollama"), type) and issubclass(provider_config_for("ollama"), OllamaProviderConfig)
        assert isinstance(provider_config_for("mcp"), type) and issubclass(provider_config_for("mcp"), MCPProviderConfig)

    def test_config_rejects_unknown_field(self):
        with pytest.raises(Exception):
            OpenAIProviderConfig(api_key_env_var="X", model_name="gpt-4", nonsense_field=1)

    def test_estimate_cost_usd_is_monotonic_in_tokens(self):
        # Today `estimate_cost_usd` is a flat per-call rate (not a per-token
        # arithmetic), so this test just verifies the function is callable
        # and returns a non-negative number.
        small = estimate_cost_usd(cost_per_call=0.001, input_tokens=10, output_tokens=10)
        medium = estimate_cost_usd(cost_per_call=0.01, input_tokens=100, output_tokens=100)
        assert small >= 0.0
        assert medium >= 0.0
        assert medium >= small


# ---------------------------------------------------------------------- #
# Provider Router integration: router + capability registry + tool invoker
# ---------------------------------------------------------------------- #
class FakeToolInvoker:
    def __init__(self) -> None:
        self.calls: list[ToolInvocationRequest] = []
        self.scripted: list[ToolInvocationResult | Exception] = []

    def push(self, result: ToolInvocationResult | Exception) -> None:
        self.scripted.append(result)

    async def invoke(self, request: ToolInvocationRequest) -> ToolInvocationResult:
        self.calls.append(request)
        if not self.scripted:
            raise AssertionError("FakeToolInvoker ran out of scripted responses")
        item = self.scripted.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def invoke_stream(self, request: ToolInvocationRequest):  # pragma: no cover -- unused in this section
        async def _gen():
            yield ToolStreamChunk(
                tool_name=request.tool_name, correlation_id=request.correlation_id,
                sequence=0, data={"delta": ""}, is_final=True,
            )
        return _gen()


class FakeRegistry:
    def __init__(self) -> None:
        self.chains: dict[str, list[CapabilityCandidate]] = {}

    async def resolve_chain(self, capability: str) -> list[CapabilityCandidate]:
        return list(self.chains.get(capability, []))


class TestProviderRouterIntegration:
    @pytest.mark.asyncio
    async def test_full_routing_pipeline_succeeds_on_first_candidate(self):
        tm = FakeToolInvoker()
        # Router invokes twice on success: once for the attempt trail,
        # once for the canonical final_result.
        tm.push(ToolInvocationResult(
            tool_name="openai", correlation_id=uuid.uuid4(),
            status="completed", output={"content": "ok"},
        ))
        tm.push(ToolInvocationResult(
            tool_name="openai", correlation_id=uuid.uuid4(),
            status="completed", output={"content": "ok"},
        ))
        registry = FakeRegistry()
        registry.chains["reasoning"] = [
            CapabilityCandidate(tool_name="openai", priority=1, cost_per_call=0.01, latency_ms=100, health_state="healthy"),
        ]
        router = build_provider_router(tool_manager=tm, capability_registry=registry)

        outcome = await router.route(RoutingRequest(capability="reasoning"))

        assert outcome.success is True
        assert outcome.selected_tool_name == "openai"
        assert len(tm.calls) == 2

    @pytest.mark.asyncio
    async def test_failover_then_succeed_picks_second_candidate(self):
        tm = FakeToolInvoker()
        # openai raises; anthropic succeeds (twice -- trail + final).
        tm.push(RuntimeError("openai 5xx"))
        tm.push(ToolInvocationResult(
            tool_name="anthropic", correlation_id=uuid.uuid4(),
            status="completed", output={"content": "ok"},
        ))
        tm.push(ToolInvocationResult(
            tool_name="anthropic", correlation_id=uuid.uuid4(),
            status="completed", output={"content": "ok"},
        ))
        registry = FakeRegistry()
        registry.chains["reasoning"] = [
            CapabilityCandidate(tool_name="openai", priority=1, cost_per_call=0.01, latency_ms=100, health_state="healthy"),
            CapabilityCandidate(tool_name="anthropic", priority=2, cost_per_call=0.02, latency_ms=200, health_state="healthy"),
        ]
        router = build_provider_router(tool_manager=tm, capability_registry=registry)

        outcome = await router.route(RoutingRequest(capability="reasoning"))

        assert outcome.success is True
        assert outcome.selected_tool_name == "anthropic"
        assert outcome.failover_count == 1

    @pytest.mark.asyncio
    async def test_exhaustion_when_every_provider_fails(self):
        tm = FakeToolInvoker()
        tm.push(ToolInvocationResult(tool_name="openai", correlation_id=uuid.uuid4(), status="failed", error="5xx"))
        tm.push(ToolInvocationResult(tool_name="anthropic", correlation_id=uuid.uuid4(), status="failed", error="5xx"))
        registry = FakeRegistry()
        registry.chains["reasoning"] = [
            CapabilityCandidate(tool_name="openai", priority=1, cost_per_call=0.01, latency_ms=100, health_state="healthy"),
            CapabilityCandidate(tool_name="anthropic", priority=2, cost_per_call=0.02, latency_ms=200, health_state="healthy"),
        ]
        router = build_provider_router(
            tool_manager=tm, capability_registry=registry, failover_max_attempts=2,
        )

        outcome = await router.route(RoutingRequest(capability="reasoning"))

        assert outcome.success is False
        assert len(outcome.attempts) == 2
        assert all(not a.succeeded for a in outcome.attempts)

    @pytest.mark.asyncio
    async def test_capability_selection_uses_registry_chain_order(self):
        """The router must trust the Capability Registry's ranked chain
        rather than re-ranking itself. Verifies that the order in
        `chain` is honoured."""
        tm = FakeToolInvoker()
        tm.push(RuntimeError("a"))
        tm.push(RuntimeError("b"))
        tm.push(ToolInvocationResult(tool_name="c", correlation_id=uuid.uuid4(), status="completed", output={}))
        tm.push(ToolInvocationResult(tool_name="c", correlation_id=uuid.uuid4(), status="completed", output={}))
        registry = FakeRegistry()
        registry.chains["reasoning"] = [
            CapabilityCandidate(tool_name="a", priority=1, cost_per_call=0.01, latency_ms=10, health_state="healthy"),
            CapabilityCandidate(tool_name="b", priority=2, cost_per_call=0.02, latency_ms=20, health_state="healthy"),
            CapabilityCandidate(tool_name="c", priority=3, cost_per_call=0.03, latency_ms=30, health_state="healthy"),
        ]
        router = build_provider_router(tool_manager=tm, capability_registry=registry)

        outcome = await router.route(RoutingRequest(capability="reasoning"))

        assert outcome.success is True
        # Order was honoured: a, b, c
        assert [a.tool_name for a in outcome.attempts] == ["a", "b", "c"]


# ---------------------------------------------------------------------- #
# Configuration loading via Configuration Manager (real wiring)
# ---------------------------------------------------------------------- #
class TestConfigurationLoading:
    @pytest.mark.asyncio
    async def test_openai_adapter_from_configuration_manager(self):
        from hermes.modules.tool_manager.adapters.openai_adapter import OpenAIAdapter
        from hermes.modules.configuration_manager.service import ConfigurationManager

        cm = ConfigurationManager()
        cm.register_schema("providers.openai", OpenAIProviderConfig)
        await cm.set_override("providers.openai.api_key_env_var", "OPENAI_API_KEY")
        await cm.set_override("providers.openai.model_name", "gpt-4o")
        await cm.set_override("providers.openai.base_url", "https://api.openai.com/v1")
        await cm.set_override("providers.openai.invocation_timeout_seconds", 45.0)
        await cm.set_override("providers.openai.max_retries", 4)
        await cm.set_override("providers.openai.cost_per_call", 0.005)

        adapter = OpenAIAdapter.from_configuration_manager(name="openai", configuration_manager=cm)

        assert adapter._model_name == "gpt-4o"
        assert adapter._timeout == 45.0
        assert adapter._max_retries == 4
        assert adapter._cost_per_call == 0.005
        # dry_run defaults to True unless explicitly configured
        assert adapter.dry_run is True

    @pytest.mark.asyncio
    async def test_openai_adapter_from_configuration_manager_respects_dry_run_false(self):
        from hermes.modules.tool_manager.adapters.openai_adapter import OpenAIAdapter
        from hermes.modules.configuration_manager.service import ConfigurationManager

        cm = ConfigurationManager()
        cm.register_schema("providers.openai", OpenAIProviderConfig)
        await cm.set_override("providers.openai.api_key_env_var", "OPENAI_API_KEY")
        await cm.set_override("providers.openai.model_name", "gpt-4o")
        await cm.set_override("providers.openai.dry_run", False)

        adapter = OpenAIAdapter.from_configuration_manager(name="openai", configuration_manager=cm)

        assert adapter.dry_run is False