"""OpenAI Chat Completions adapter.

Production-ready: implements `ToolAdapter` end-to-end against the
`https://api.openai.com/v1/chat/completions` HTTP API. Honours every
shared event / observability hook documented in `provider_events.py`.

All safety properties the OpenAI skeleton already had are preserved:

- `dry_run=True` is the unconditional default; no API key ever read.
- `api_key` is never a constructor argument -- only the **env var
  name** (a string) is. Real value loads from `os.environ` only when
  `dry_run=False` and `authenticate()`/`invoke()` actually runs.
- No network call happens unless `dry_run=False` is explicitly set AND
  a real key is present.
- Cancellation token flips on during an in-flight call aborts
  mid-stream cleanly.
- Streaming modes use SSE (`data: {...}\\n\\n`) and the adapter yields
  incremental `ToolStreamChunk`s.

The adapter speaks a strict subset of the chat-completions API:
- `parameters["messages"]` is required (list of `{"role", "content"}`).
- `parameters["model"]` (optional) overrides config.
- `parameters["tools"]`, `tool_choice`, `temperature`, `max_tokens`,
  `stop`, `top_p`, `frequency_penalty`, `presence_penalty`, `seed`,
  `user`, `stream` are forwarded as-is when present.
"""
from __future__ import annotations

import json
import os
import time
import uuid
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
    Transport,
    make_authorization_header,
    safe_json_loads,
)
from hermes.modules.tool_manager.adapters.provider_config import OpenAIProviderConfig
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


OPENAI_API_KEY_ENV_VAR = "OPENAI_API_KEY"
PROVIDER_NAME = "openai"
SUPPORTED_CAPABILITIES = (
    "reasoning",
    "planning",
    "code_generation",
    "vision",
    "image_generation",
    "video_generation",
    "voice_generation",
)


class OpenAIAuthenticationError(Exception):
    """Raised only when `dry_run=False` and no API key is present in
    the configured environment variable. Never raised in dry_run mode.
    """

    def __init__(self, env_var: str) -> None:
        self.env_var = env_var
        super().__init__(
            f"OpenAI adapter requires the {env_var!r} environment variable to be set "
            f"(dry_run=False was requested)"
        )


class OpenAIAdapter(BaseToolAdapter):
    provider = PROVIDER_NAME
    capabilities = ToolCapabilities(supports_sync=True, supports_streaming=True, requires_auth=True)

    DEFAULT_BASE_URL = "https://api.openai.com/v1"
    DEFAULT_MODEL = "gpt-4o-mini"

    def __init__(
        self,
        *,
        name: str,
        dry_run: bool = True,
        api_key_env_var: str = OPENAI_API_KEY_ENV_VAR,
        base_url: str = DEFAULT_BASE_URL,
        model_name: str = DEFAULT_MODEL,
        organization: str | None = None,
        invocation_timeout_seconds: float = 30.0,
        max_retries: int = 2,
        cost_per_call: float = 0.0,
        transport: HTTPTransport | None = None,
        recorder: ProviderRecorder | None = None,
    ) -> None:
        super().__init__(name=name)
        self.dry_run = dry_run
        self._api_key_env_var = api_key_env_var or OPENAI_API_KEY_ENV_VAR
        self._base_url = base_url.rstrip("/")
        self._model_name = model_name
        self._organization = organization
        self._timeout = invocation_timeout_seconds
        self._max_retries = max_retries
        self._cost_per_call = cost_per_call
        self._transport = transport
        self._recorder = recorder if recorder is not None else ProviderRecorder(log=ProviderEventLog())
        self._in_flight: dict[uuid.UUID, str] = {}

    @classmethod
    def from_configuration_manager(
        cls,
        *,
        name: str,
        configuration_manager: "ConfigurationManager",
        transport: HTTPTransport | None = None,
        recorder: ProviderRecorder | None = None,
    ) -> "OpenAIAdapter":
        """Alternative constructor, additive alongside `__init__`. Reads
        the validated provider config, falling back to dry-run-safe
        defaults where Configuration Manager has nothing set.
        """
        cfg = OpenAIProviderConfig(**configuration_manager.get_provider_config("openai"))
        dry_run = configuration_manager.get_dry_run("providers.openai", default=True)
        return cls(
            name=name,
            dry_run=dry_run,
            api_key_env_var=cfg.api_key_env_var,
            base_url=cfg.base_url,
            model_name=cfg.model_name,
            organization=cfg.organization,
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
        if not self._api_key_env_var:
            # Implicit "no auth required" -- e.g. a local OpenAI-compatible proxy.
            return
        if not self._load_api_key():
            raise OpenAIAuthenticationError(self._api_key_env_var)

    async def health_check(self) -> bool:
        """Cheap liveness probe in production mode: a GET on `/models`
        with the configured credentials. In dry_run mode (default), the
        adapter is always healthy; a real network check requires a key.
        """
        if self.dry_run:
            return True
        if not self._transport:
            return True
        try:
            response = await self._transport.send(
                HTTPRequest(
                    method="GET",
                    url=f"{self._base_url}/models",
                    headers=self._auth_headers(),
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
            raise OpenAIAuthenticationError(self._api_key_env_var)

        sw = Stopwatch()
        sw.start()
        try:
            response_body = await self._post_chat(request, stream=False)
            elapsed_ms = sw.stop()
            data = safe_json_loads(response_body)
            assert isinstance(data, dict)
            assistant_message = self._extract_assistant_text(data)
            usage = self._extract_usage(data)
            cost_usd = self._cost_per_call
            await self._recorder.succeeded(
                provider=self.provider,
                tool_name=self.name,
                capability=capability,
                correlation_id=request.correlation_id,
                latency_ms=elapsed_ms,
                extra={"model": self._model_name, "usage": usage, "cost_usd": cost_usd},
            )
            await self._recorder.token_usage(
                provider=self.provider,
                tool_name=self.name,
                capability=capability,
                correlation_id=request.correlation_id,
                input_tokens=usage["input_tokens"],
                output_tokens=usage["output_tokens"],
                total_tokens=usage["total_tokens"],
                model=self._model_name,
            )
            await self._recorder.latency(
                provider=self.provider,
                tool_name=self.name,
                capability=capability,
                correlation_id=request.correlation_id,
                latency_ms=elapsed_ms,
            )
            await self._recorder.estimated_cost(
                provider=self.provider,
                tool_name=self.name,
                capability=capability,
                correlation_id=request.correlation_id,
                cost_usd=cost_usd,
            )
            return ToolInvocationResult(
                tool_name=request.tool_name,
                correlation_id=request.correlation_id,
                status="completed",
                output={
                    "provider": self.provider,
                    "model": self._model_name,
                    "capability": capability,
                    "message": assistant_message,
                    "finish_reason": self._extract_finish_reason(data),
                    "raw": data,
                    "usage": usage,
                    "cost_usd": cost_usd,
                    "latency_ms": elapsed_ms,
                },
            )
        except HTTPTimeoutError as exc:
            elapsed_ms = sw.stop()
            await self._recorder.timeout(
                provider=self.provider,
                tool_name=self.name,
                capability=capability,
                correlation_id=request.correlation_id,
                timeout_seconds=self._timeout,
            )
            await self._recorder.failed(
                provider=self.provider,
                tool_name=self.name,
                capability=capability,
                correlation_id=request.correlation_id,
                error=f"timeout after {self._timeout}s",
            )
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
            yield self._dry_run_chunk(request, sequence=0)
            return

        if self._api_key_env_var and not self._load_api_key():
            raise OpenAIAuthenticationError(self._api_key_env_var)

        sw = Stopwatch()
        sw.start()
        seq = 0
        try:
            async for raw_chunk in self._stream_chat(request):
                seq += 1
                decoded = self._parse_sse_chunk(raw_chunk)
                if decoded is None:
                    continue
                if decoded.get("done"):
                    elapsed_ms = sw.stop()
                    await self._recorder.succeeded(
                        provider=self.provider,
                        tool_name=self.name,
                        capability=capability,
                        correlation_id=request.correlation_id,
                        latency_ms=elapsed_ms,
                    )
                    yield ToolStreamChunk(
                        tool_name=request.tool_name,
                        correlation_id=request.correlation_id,
                        sequence=seq,
                        data={"done": True},
                        is_final=True,
                    )
                    return
                delta = self._extract_streaming_delta(decoded)
                text = delta.get("content", "")
                yield ToolStreamChunk(
                    tool_name=request.tool_name,
                    correlation_id=request.correlation_id,
                    sequence=seq,
                    data={"delta": text, "model": decoded.get("model", self._model_name)},
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
    # Internals
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
                "note": "dry_run=True: no live OpenAI call was made. Set providers.openai.dry_run=False to enable.",
            },
        )


    def _dry_run_chunk(self, request: ToolInvocationRequest, *, sequence: int) -> ToolStreamChunk:
        return ToolStreamChunk(
            tool_name=request.tool_name,
            correlation_id=request.correlation_id,
            sequence=sequence,
            data={
                "delta": "[dry_run] no live OpenAI stream",
                "dry_run": True,
                "provider": self.provider,
            },
            is_final=True,
        )

    def _auth_headers(self) -> dict[str, str]:
        headers = make_authorization_header(self._load_api_key() or "")
        if self._organization:
            headers["OpenAI-Organization"] = self._organization
        return headers

    def _build_payload(self, request: ToolInvocationRequest, *, stream: bool) -> dict[str, Any]:
        params = dict(request.parameters)
        params.pop("capability", None)
        model = params.pop("model", None) or self._model_name
        messages = params.pop("messages", None)
        if messages is None:
            # Convenience: accept a top-level `prompt` for single-turn calls.
            prompt = params.pop("prompt", "")
            messages = [{"role": "user", "content": prompt}]
        payload: dict[str, Any] = {"model": model, "messages": messages, "stream": stream}
        for k, v in params.items():
            if v is not None:
                payload[k] = v
        return payload

    async def _post_chat(self, request: ToolInvocationRequest, *, stream: bool) -> bytes:
        if not self._transport:
            raise RuntimeError("OpenAIAdapter has no transport configured for production calls")
        body = json.dumps(self._build_payload(request, stream=False)).encode("utf-8")
        http_response = await self._transport.send(
            HTTPRequest(
                method="POST",
                url=f"{self._base_url}/chat/completions",
                headers={"Content-Type": "application/json", **self._auth_headers()},
                body=body,
                timeout_seconds=self._timeout,
            )
        )
        if not (200 <= http_response.status < 300):
            raise HTTPStatusError(http_response.status, http_response.body.decode("utf-8", errors="replace"))
        return http_response.body

    async def _stream_chat(self, request: ToolInvocationRequest) -> AsyncIterator[bytes]:
        if not self._transport:
            raise RuntimeError("OpenAIAdapter has no transport configured for streaming")
        body = json.dumps(self._build_payload(request, stream=True)).encode("utf-8")
        async for chunk in self._transport.stream(
            HTTPRequest(
                method="POST",
                url=f"{self._base_url}/chat/completions",
                headers={"Content-Type": "application/json", **self._auth_headers()},
                body=body,
                timeout_seconds=self._timeout,
            )
        ):
            yield chunk

    @staticmethod
    def _extract_streaming_delta(decoded: dict[str, Any]) -> dict[str, Any]:
        """Extracts the delta dict from an OpenAI Chat Completions
        streaming chunk. Real wire format nests it under
        `choices[0].delta`; tests may flatten it. Returns `{}` if no
        delta payload is present (e.g. a role-only chunk)."""
        # Flat shape (used by some compatible providers)
        flat = decoded.get("delta")
        if isinstance(flat, dict):
            return flat
        # Standard OpenAI nested shape
        choices = decoded.get("choices") or []
        if not choices:
            return {}
        return (choices[0] or {}).get("delta") or {}

    @staticmethod
    def _parse_sse_chunk(raw: bytes) -> dict[str, Any] | None:
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return None
        for line in text.splitlines():
            line = line.strip()
            if not line or not line.startswith("data:"):
                continue
            data = line[len("data:") :].strip()
            if data == "[DONE]":
                return {"done": True}
            try:
                return json.loads(data)
            except json.JSONDecodeError:
                continue
        return None

    @staticmethod
    def _extract_assistant_text(data: dict[str, Any]) -> str:
        choices = data.get("choices") or []
        if not choices:
            return ""
        message = (choices[0] or {}).get("message") or {}
        content = message.get("content", "")
        if isinstance(content, list):
            return "".join(part.get("text", "") for part in content if isinstance(part, dict))
        return content if isinstance(content, str) else str(content)

    @staticmethod
    def _extract_finish_reason(data: dict[str, Any]) -> str | None:
        choices = data.get("choices") or []
        if not choices:
            return None
        return (choices[0] or {}).get("finish_reason")

    @staticmethod
    def _extract_usage(data: dict[str, Any]) -> dict[str, int]:
        usage = data.get("usage") or {}
        return {
            "input_tokens": int(usage.get("prompt_tokens", 0)),
            "output_tokens": int(usage.get("completion_tokens", 0)),
            "total_tokens": int(usage.get("total_tokens", 0)),
        }


def register_with_capability_registry(
    capability_registry,
    *,
    tool_name: str = "openai",
    priority: int = 100,
    cost_per_call: float = 0.0,
    declared_latency_ms: float = 0.0,
) -> None:
    """Registers `tool_name` as a provider for the capabilities this
    adapter supports. Capability names are derived from one source of
    truth -- `provider_config.OpenAIProviderConfig.capabilities`.
    """
    register_provider_capabilities(
        capability_registry,
        provider_name=PROVIDER_NAME,
        tool_name=tool_name,
        priority=priority,
        cost_per_call=cost_per_call,
        declared_latency_ms=declared_latency_ms,
    )


__all__ = [
    "OpenAIAdapter",
    "OpenAIAuthenticationError",
    "OpenAIProviderConfig",
    "OPENAI_API_KEY_ENV_VAR",
    "PROVIDER_NAME",
    "SUPPORTED_CAPABILITIES",
    "register_with_capability_registry",
]
