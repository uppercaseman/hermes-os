"""Ollama-compatible local model adapter.

Production-ready. Implements `ToolAdapter` end-to-end against the
`POST {base_url}/api/chat` endpoint exposed by Ollama, LM Studio, vLLM,
llama.cpp's HTTP server, and any other OpenAI-Ollama-shape local
inference server.

The default base URL `http://localhost:11434` is Ollama; LM Studio
defaults to `http://localhost:1234` -- override via Configuration
Manager.
"""
from __future__ import annotations

import json
import os
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
    make_authorization_header,
    safe_json_loads,
)
from hermes.modules.tool_manager.adapters.provider_config import OllamaProviderConfig
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
    from hermes.modules.configuration_manager.interface import ConfigurationManager


PROVIDER_NAME = "ollama"
DEFAULT_BASE_URL = "http://localhost:11434"
DEFAULT_MODEL = "llama3.1"
SUPPORTED_CAPABILITIES = ("reasoning", "planning", "code_generation", "vision")


class OllamaAdapter(BaseToolAdapter):
    provider = PROVIDER_NAME
    capabilities = ToolCapabilities(supports_sync=True, supports_streaming=True, requires_auth=False)

    def __init__(
        self,
        *,
        name: str,
        dry_run: bool = True,
        api_key_env_var: str = "",
        base_url: str = DEFAULT_BASE_URL,
        model_name: str = DEFAULT_MODEL,
        keep_alive_minutes: int = 5,
        invocation_timeout_seconds: float = 60.0,
        max_retries: int = 2,
        cost_per_call: float = 0.0,
        transport: HTTPTransport | None = None,
        recorder: ProviderRecorder | None = None,
    ) -> None:
        super().__init__(name=name)
        self.dry_run = dry_run
        self._api_key_env_var = api_key_env_var  # local servers often need none
        self._base_url = base_url.rstrip("/")
        self._model_name = model_name
        self._keep_alive_minutes = keep_alive_minutes
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
    ) -> "OllamaAdapter":
        cfg = OllamaProviderConfig(**configuration_manager.get_provider_config("ollama"))
        dry_run = configuration_manager.get_dry_run("providers.ollama", default=True)
        return cls(
            name=name,
            dry_run=dry_run,
            api_key_env_var=cfg.api_key_env_var,
            base_url=cfg.base_url,
            model_name=cfg.model_name,
            keep_alive_minutes=cfg.keep_alive_minutes,
            invocation_timeout_seconds=cfg.invocation_timeout_seconds,
            max_retries=cfg.max_retries,
            cost_per_call=cfg.cost_per_call,
            transport=transport,
            recorder=recorder,
        )

    def _load_api_key(self) -> str | None:
        return os.environ.get(self._api_key_env_var) if self._api_key_env_var else None

    async def authenticate(self) -> None:
        # Local servers typically don't require auth. If `api_key_env_var`
        # was configured, we still require that variable to be set, mirroring
        # the cloud providers' posture.
        if self.dry_run:
            return
        if self._api_key_env_var and not self._load_api_key():
            from hermes.modules.tool_manager.adapters.openai_adapter import OpenAIAuthenticationError
            raise OpenAIAuthenticationError(self._api_key_env_var)

    async def health_check(self) -> bool:
        if self.dry_run:
            return True
        if not self._transport:
            return True
        try:
            response = await self._transport.send(
                HTTPRequest(
                    method="GET", url=f"{self._base_url}/api/tags",
                    headers=self._headers(), timeout_seconds=min(5.0, self._timeout),
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
            from hermes.modules.tool_manager.adapters.openai_adapter import OpenAIAuthenticationError
            raise OpenAIAuthenticationError(self._api_key_env_var)

        sw = Stopwatch()
        sw.start()
        try:
            body = await self._post_chat(request, stream=False)
            elapsed_ms = sw.stop()
            data = safe_json_loads(body)
            assert isinstance(data, dict)
            text = self._extract_message(data)
            usage = self._extract_usage(data)
            cost_usd = self._cost_per_call  # typically zero for local models
            await self._emit_success(request.correlation_id, capability, elapsed_ms, usage, cost_usd)
            return ToolInvocationResult(
                tool_name=request.tool_name,
                correlation_id=request.correlation_id,
                status="completed",
                output={
                    "provider": self.provider, "model": self._model_name, "capability": capability,
                    "message": text, "raw": data, "usage": usage, "cost_usd": cost_usd,
                    "latency_ms": elapsed_ms,
                },
            )
        except HTTPTimeoutError:
            await self._emit_timeout(request.correlation_id, capability)
            raise
        except HTTPStatusError as exc:
            await self._recorder.failed(
                provider=self.provider, tool_name=self.name, capability=capability,
                correlation_id=request.correlation_id, error=str(exc),
            )
            raise

    async def invoke_stream(self, request: ToolInvocationRequest) -> AsyncIterator[ToolStreamChunk]:
        capability = str(request.parameters.get("capability", "reasoning"))
        if self.dry_run:
            yield ToolStreamChunk(
                tool_name=request.tool_name, correlation_id=request.correlation_id, sequence=0,
                data={"delta": "[dry_run] no live Ollama stream", "dry_run": True, "provider": self.provider},
                is_final=True,
            )
            return
        if not self._transport:
            yield ToolStreamChunk(
                tool_name=request.tool_name, correlation_id=request.correlation_id,
                sequence=0, is_final=True, error="no transport configured",
            )
            return

        body = json.dumps(self._build_payload(request, stream=True)).encode("utf-8")
        seq = 0
        try:
            async for raw in self._transport.stream(
                HTTPRequest(
                    method="POST", url=f"{self._base_url}/api/chat",
                    headers={"Content-Type": "application/json", **self._headers()},
                    body=body, timeout_seconds=self._timeout,
                )
            ):
                for ev in self._parse_ndjson(raw):
                    seq += 1
                    if ev.get("done"):
                        yield ToolStreamChunk(
                            tool_name=request.tool_name, correlation_id=request.correlation_id,
                            sequence=seq, data={"done": True, "raw": ev}, is_final=True,
                        )
                        return
                    delta = self._extract_message_delta(ev)
                    yield ToolStreamChunk(
                        tool_name=request.tool_name, correlation_id=request.correlation_id,
                        sequence=seq, data={"delta": delta, "raw": ev},
                    )
        except Exception as exc:
            yield ToolStreamChunk(
                tool_name=request.tool_name, correlation_id=request.correlation_id,
                sequence=seq, is_final=True, error=str(exc),
            )

    # ------------------------------------------------------------------ #
    def _dry_run_result(self, request: ToolInvocationRequest, capability: str) -> ToolInvocationResult:
        return ToolInvocationResult(
            tool_name=request.tool_name,
            correlation_id=request.correlation_id,
            status="completed",
            output={
                "dry_run": True, "provider": self.provider, "model": self._model_name,
                "capability": capability, "operation": request.operation,
                "echo_parameters": request.parameters,
                "note": "dry_run=True: no live local inference call was made.",
            },
        )

    def _headers(self) -> dict[str, str]:
        api_key = self._load_api_key() or ""
        return make_authorization_header(api_key) if api_key else {}

    def _build_payload(self, request: ToolInvocationRequest, *, stream: bool) -> dict[str, Any]:
        params = dict(request.parameters)
        params.pop("capability", None)
        model = params.pop("model", None) or self._model_name
        messages = params.pop("messages", None)
        if messages is None:
            prompt = params.pop("prompt", "")
            messages = [{"role": "user", "content": prompt}]
        payload: dict[str, Any] = {
            "model": model, "messages": messages, "stream": stream,
            "keep_alive": f"{self._keep_alive_minutes}m",
        }
        for k, v in params.items():
            if v is not None:
                payload[k] = v
        return payload

    async def _post_chat(self, request: ToolInvocationRequest, *, stream: bool) -> bytes:
        if not self._transport:
            raise RuntimeError("OllamaAdapter has no transport configured for production calls")
        body = json.dumps(self._build_payload(request, stream=stream)).encode("utf-8")
        response = await self._transport.send(
            HTTPRequest(
                method="POST", url=f"{self._base_url}/api/chat",
                headers={"Content-Type": "application/json", **self._headers()},
                body=body, timeout_seconds=self._timeout,
            )
        )
        if not (200 <= response.status < 300):
            raise HTTPStatusError(response.status, response.body.decode("utf-8", errors="replace"))
        return response.body

    @staticmethod
    def _parse_ndjson(raw: bytes) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return events
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return events

    @staticmethod
    def _extract_message(data: dict[str, Any]) -> str:
        msg = data.get("message") or {}
        return msg.get("content", "")

    @staticmethod
    def _extract_message_delta(data: dict[str, Any]) -> str:
        msg = data.get("message") or {}
        return msg.get("content", "")

    @staticmethod
    def _extract_usage(data: dict[str, Any]) -> dict[str, int]:
        return {
            "input_tokens": int((data.get("prompt_eval_count") or 0)),
            "output_tokens": int((data.get("eval_count") or 0)),
            "total_tokens": int((data.get("prompt_eval_count") or 0)) + int((data.get("eval_count") or 0)),
        }

    async def _emit_success(self, cid, capability, elapsed_ms, usage, cost_usd):
        await self._recorder.succeeded(
            provider=self.provider, tool_name=self.name, capability=capability,
            correlation_id=cid, latency_ms=elapsed_ms,
            extra={"model": self._model_name, "usage": usage, "cost_usd": cost_usd},
        )
        await self._recorder.token_usage(
            provider=self.provider, tool_name=self.name, capability=capability,
            correlation_id=cid, input_tokens=usage["input_tokens"],
            output_tokens=usage["output_tokens"], total_tokens=usage["total_tokens"],
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
    tool_name: str = "ollama",
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
    "OllamaAdapter",
    "OllamaProviderConfig",
    "PROVIDER_NAME",
    "DEFAULT_BASE_URL",
    "DEFAULT_MODEL",
    "SUPPORTED_CAPABILITIES",
    "register_with_capability_registry",
]