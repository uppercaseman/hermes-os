"""Anthropic Claude (Messages API) adapter.

Production-ready. Implements `ToolAdapter` end-to-end against
`POST {base_url}/v1/messages`. Translates Hermes-style
`{messages: [{role, content}, ...]}` (with system prompts surfaced
separately when present in `parameters["system"]`) into the Anthropic
`{system, messages, max_tokens, model}` shape, and converts the
response back into a Hermes-shaped `ToolInvocationResult`.

The streaming endpoint is the Messages `stream` event protocol; chunks
are emitted as `ToolStreamChunk`s with `data["delta"]` carrying the
incremental text and `data["type"]` carrying the Anthropic event name
(`message_start`, `content_block_delta`, `message_delta`, `message_stop`).
"""
from __future__ import annotations

import json
import os
import time
from typing import TYPE_CHECKING, Any, AsyncIterator

from hermes.modules.tool_manager.adapters.base import BaseToolAdapter
from hermes.modules.tool_manager.adapters.capability_registration import (
    register_provider_capabilities,
)
from hermes.modules.tool_manager.adapters.http_base import (
    HTTPRequest,
    HTTPStatusError,
    HTTPTimeoutError,
    StdlibHTTPTransport as HTTPTransport,
    safe_json_loads,
)
from hermes.modules.tool_manager.adapters.provider_config import AnthropicProviderConfig
from hermes.modules.tool_manager.adapters.provider_events import (
    ProviderEventLog,
    ProviderRecorder,
    Stopwatch,
)
from hermes.modules.tool_manager.models import (
    ToolCapabilities,
    ToolInvocationRequest,
    ToolInvocationResult,
    ToolStreamChunk,
)

if TYPE_CHECKING:
    from hermes.core.event_bus.interface import EventBus
    from hermes.modules.configuration_manager.interface import ConfigurationManager


ANTHROPIC_API_KEY_ENV_VAR = "ANTHROPIC_API_KEY"
PROVIDER_NAME = "anthropic"
DEFAULT_BASE_URL = "https://api.anthropic.com"
DEFAULT_MODEL = "claude-3-5-sonnet-latest"
SUPPORTED_CAPABILITIES = ("reasoning", "planning", "code_generation", "vision")


class AnthropicAuthenticationError(Exception):
    def __init__(self, env_var: str) -> None:
        self.env_var = env_var
        super().__init__(
            f"Anthropic adapter requires {env_var!r} (dry_run=False was requested)"
        )


class ClaudeAdapter(BaseToolAdapter):
    provider = PROVIDER_NAME
    capabilities = ToolCapabilities(supports_sync=True, supports_streaming=True, requires_auth=True)

    def __init__(
        self,
        *,
        name: str,
        dry_run: bool = True,
        api_key_env_var: str = ANTHROPIC_API_KEY_ENV_VAR,
        base_url: str = DEFAULT_BASE_URL,
        model_name: str = DEFAULT_MODEL,
        anthropic_version: str = "2023-06-01",
        max_tokens: int = 4096,
        invocation_timeout_seconds: float = 30.0,
        max_retries: int = 2,
        cost_per_call: float = 0.0,
        transport: HTTPTransport | None = None,
        recorder: ProviderRecorder | None = None,
    ) -> None:
        super().__init__(name=name)
        self.dry_run = dry_run
        self._api_key_env_var = api_key_env_var or ANTHROPIC_API_KEY_ENV_VAR
        self._base_url = base_url.rstrip("/")
        self._model_name = model_name
        self._anthropic_version = anthropic_version
        self._max_tokens = max_tokens
        self._timeout = invocation_timeout_seconds
        self._max_retries = max_retries
        self._cost_per_call = cost_per_call
        self._transport = transport
        self._recorder = recorder if recorder is not None else ProviderRecorder(log=ProviderEventLog())

    @classmethod
    def from_configuration_manager(
        cls,
        *,
        name: str,
        configuration_manager: "ConfigurationManager",
        transport: HTTPTransport | None = None,
        recorder: ProviderRecorder | None = None,
    ) -> "ClaudeAdapter":
        cfg = AnthropicProviderConfig(**configuration_manager.get_provider_config("anthropic"))
        dry_run = configuration_manager.get_dry_run("providers.anthropic", default=True)
        return cls(
            name=name,
            dry_run=dry_run,
            api_key_env_var=cfg.api_key_env_var,
            base_url=cfg.base_url,
            model_name=cfg.model_name,
            anthropic_version=cfg.anthropic_version,
            max_tokens=cfg.max_tokens,
            invocation_timeout_seconds=cfg.invocation_timeout_seconds,
            max_retries=cfg.max_retries,
            cost_per_call=cfg.cost_per_call,
            transport=transport,
            recorder=recorder,
        )

    def _load_api_key(self) -> str | None:
        return os.environ.get(self._api_key_env_var) if self._api_key_env_var else None

    async def authenticate(self) -> None:
        if self.dry_run:
            return
        if self._api_key_env_var and not self._load_api_key():
            raise AnthropicAuthenticationError(self._api_key_env_var)

    async def health_check(self) -> bool:
        if self.dry_run:
            return True
        if not self._transport:
            return True
        try:
            response = await self._transport.send(
                HTTPRequest(
                    method="GET",
                    url=f"{self._base_url}/v1/models",
                    headers=self._headers(),
                    timeout_seconds=min(5.0, self._timeout),
                )
            )
            return 200 <= response.status < 300
        except Exception:
            return False

    async def invoke(self, request: ToolInvocationRequest) -> ToolInvocationResult:
        capability = str(request.parameters.get("capability", "reasoning"))
        if self.dry_run:
            return self._dry_run_result(request, capability)
        if self._api_key_env_var and not self._load_api_key():
            raise AnthropicAuthenticationError(self._api_key_env_var)

        sw = Stopwatch()
        sw.start()
        try:
            payload = self._build_payload(request, stream=False)
            response_body = await self._post_messages(payload)
            elapsed_ms = sw.stop()
            data = safe_json_loads(response_body)
            assert isinstance(data, dict)
            text = self._extract_text(data)
            usage = self._extract_usage(data)
            cost_usd = self._cost_per_call
            await self._emit_success(request.correlation_id, capability, elapsed_ms, usage, cost_usd)
            return ToolInvocationResult(
                tool_name=request.tool_name,
                correlation_id=request.correlation_id,
                status="completed",
                output={
                    "provider": self.provider,
                    "model": self._model_name,
                    "capability": capability,
                    "message": text,
                    "stop_reason": data.get("stop_reason"),
                    "raw": data,
                    "usage": usage,
                    "cost_usd": cost_usd,
                    "latency_ms": elapsed_ms,
                },
            )
        except HTTPTimeoutError:
            await self._emit_timeout(request.correlation_id, capability)
            raise
        except HTTPStatusError as exc:
            await self._recorder.failed(
                provider=self.provider,
                tool_name=self.name,
                capability=capability,
                correlation_id=request.correlation_id,
                error=str(exc),
            )
            raise

    async def invoke_stream(self, request: ToolInvocationRequest) -> AsyncIterator[ToolStreamChunk]:
        capability = str(request.parameters.get("capability", "reasoning"))
        if self.dry_run:
            yield ToolStreamChunk(
                tool_name=request.tool_name,
                correlation_id=request.correlation_id,
                sequence=0,
                data={"delta": "[dry_run] no live Anthropic stream", "dry_run": True, "provider": self.provider},
                is_final=True,
            )
            return
        if not self._transport:
            yield ToolStreamChunk(
                tool_name=request.tool_name,
                correlation_id=request.correlation_id,
                sequence=0,
                is_final=True,
                error="no transport configured",
            )
            return

        payload = self._build_payload(request, stream=True)
        body = json.dumps(payload).encode("utf-8")
        seq = 0
        try:
            async for raw in self._transport.stream(
                HTTPRequest(
                    method="POST",
                    url=f"{self._base_url}/v1/messages",
                    headers={"Content-Type": "application/json", **self._headers()},
                    body=body,
                    timeout_seconds=self._timeout,
                )
            ):
                for event in self._parse_sse_events(raw):
                    seq += 1
                    if event.get("type") == "message_stop":
                        yield ToolStreamChunk(
                            tool_name=request.tool_name,
                            correlation_id=request.correlation_id,
                            sequence=seq,
                            data={"event": event, "done": True},
                            is_final=True,
                        )
                        return
                    delta_text = ""
                    if event.get("type") == "content_block_delta":
                        delta = (event.get("delta") or {}).get("text", "")
                        delta_text = delta
                    yield ToolStreamChunk(
                        tool_name=request.tool_name,
                        correlation_id=request.correlation_id,
                        sequence=seq,
                        data={"event_type": event.get("type"), "delta": delta_text},
                    )
        except Exception as exc:
            yield ToolStreamChunk(
                tool_name=request.tool_name,
                correlation_id=request.correlation_id,
                sequence=seq,
                is_final=True,
                error=str(exc),
            )

    # ------------------------------------------------------------------ #
    def _dry_run_result(self, request: ToolInvocationRequest, capability: str) -> ToolInvocationResult:
        return ToolInvocationResult(
            tool_name=request.tool_name,
            correlation_id=request.correlation_id,
            status="completed",
            output={
                "dry_run": True,
                "provider": self.provider,
                "model": self._model_name,
                "capability": capability,
                "operation": request.operation,
                "echo_parameters": request.parameters,
                "note": "dry_run=True: no live Anthropic call was made.",
            },
        )

    def _headers(self) -> dict[str, str]:
        api_key = self._load_api_key() or ""
        return {
            "x-api-key": api_key,
            "anthropic-version": self._anthropic_version,
            "Authorization": f"Bearer {api_key}" if api_key else "",
        }

    def _build_payload(self, request: ToolInvocationRequest, *, stream: bool) -> dict[str, Any]:
        params = dict(request.parameters)
        params.pop("capability", None)
        system_prompt = params.pop("system", None)
        messages = params.pop("messages", None)
        if messages is None:
            prompt = params.pop("prompt", "")
            messages = [{"role": "user", "content": prompt}]
        model = params.pop("model", None) or self._model_name
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": params.pop("max_tokens", self._max_tokens),
            "stream": stream,
        }
        if system_prompt:
            payload["system"] = system_prompt
        for k, v in params.items():
            if v is not None:
                payload[k] = v
        return payload

    async def _post_messages(self, payload: dict[str, Any]) -> bytes:
        if not self._transport:
            raise RuntimeError("ClaudeAdapter has no transport configured for production calls")
        body = json.dumps(payload).encode("utf-8")
        response = await self._transport.send(
            HTTPRequest(
                method="POST",
                url=f"{self._base_url}/v1/messages",
                headers={"Content-Type": "application/json", **self._headers()},
                body=body,
                timeout_seconds=self._timeout,
            )
        )
        if not (200 <= response.status < 300):
            raise HTTPStatusError(response.status, response.body.decode("utf-8", errors="replace"))
        return response.body

    @staticmethod
    def _parse_sse_events(raw: bytes) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return events
        for line in text.splitlines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            data = line[len("data:") :].strip()
            if not data or data == "[DONE]":
                continue
            try:
                events.append(json.loads(data))
            except json.JSONDecodeError:
                continue
        return events

    @staticmethod
    def _extract_text(data: dict[str, Any]) -> str:
        parts: list[str] = []
        for block in data.get("content") or []:
            if (block or {}).get("type") == "text":
                parts.append(block.get("text", ""))
        return "".join(parts)

    @staticmethod
    def _extract_usage(data: dict[str, Any]) -> dict[str, int]:
        usage = data.get("usage") or {}
        return {
            "input_tokens": int(usage.get("input_tokens", 0)),
            "output_tokens": int(usage.get("output_tokens", 0)),
            "total_tokens": int(usage.get("input_tokens", 0)) + int(usage.get("output_tokens", 0)),
        }

    async def _emit_success(self, cid, capability, elapsed_ms, usage, cost_usd):
        await self._recorder.succeeded(
            provider=self.provider,
            tool_name=self.name,
            capability=capability,
            correlation_id=cid,
            latency_ms=elapsed_ms,
            extra={"model": self._model_name, "usage": usage, "cost_usd": cost_usd},
        )
        await self._recorder.token_usage(
            provider=self.provider,
            tool_name=self.name,
            capability=capability,
            correlation_id=cid,
            input_tokens=usage["input_tokens"],
            output_tokens=usage["output_tokens"],
            total_tokens=usage["total_tokens"],
            model=self._model_name,
        )
        await self._recorder.latency(
            provider=self.provider, tool_name=self.name, capability=capability,
            correlation_id=cid, latency_ms=elapsed_ms,
        )
        await self._recorder.estimated_cost(
            provider=self.provider, tool_name=self.name, capability=capability,
            correlation_id=cid, cost_usd=cost_usd,
        )

    async def _emit_timeout(self, cid, capability):
        await self._recorder.timeout(
            provider=self.provider, tool_name=self.name, capability=capability,
            correlation_id=cid, timeout_seconds=self._timeout,
        )
        await self._recorder.failed(
            provider=self.provider, tool_name=self.name, capability=capability,
            correlation_id=cid, error=f"timeout after {self._timeout}s",
        )


def register_with_capability_registry(
    capability_registry,
    *,
    tool_name: str = "anthropic",
    priority: int = 100,
    cost_per_call: float = 0.0,
    declared_latency_ms: float = 0.0,
) -> None:
    register_provider_capabilities(
        capability_registry,
        provider_name=PROVIDER_NAME,
        tool_name=tool_name,
        priority=priority,
        cost_per_call=cost_per_call,
        declared_latency_ms=declared_latency_ms,
    )


__all__ = [
    "ClaudeAdapter",
    "AnthropicAuthenticationError",
    "AnthropicProviderConfig",
    "ANTHROPIC_API_KEY_ENV_VAR",
    "PROVIDER_NAME",
    "DEFAULT_MODEL",
    "DEFAULT_BASE_URL",
    "SUPPORTED_CAPABILITIES",
    "register_with_capability_registry",
]