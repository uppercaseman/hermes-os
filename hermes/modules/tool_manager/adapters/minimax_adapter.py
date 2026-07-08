"""MiniMax Chat Completions adapter (OpenAI-compatible).

Production-ready. Implements `ToolAdapter` end-to-end against the
`/v1/text/chatcompletion_v2` (or, when configured, the OpenAI-compatible
`/v1/chat/completions`) endpoint exposed by MiniMax.

The MiniMax API shape is the OpenAI Chat Completions shape with a
`group_id` header. Behaviour for `dry_run`, retries, timeouts, and
streaming mirrors `OpenAIAdapter` exactly.
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
from hermes.modules.tool_manager.adapters.provider_config import MiniMaxProviderConfig
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


MINIMAX_API_KEY_ENV_VAR = "MINIMAX_API_KEY"
PROVIDER_NAME = "minimax"
DEFAULT_BASE_URL = "https://api.minimax.chat/v1"
DEFAULT_MODEL = "abab6.5s-chat"
SUPPORTED_CAPABILITIES = ("reasoning", "planning", "code_generation", "vision")


class MiniMaxAuthenticationError(Exception):
    def __init__(self, env_var: str) -> None:
        self.env_var = env_var
        super().__init__(
            f"MiniMax adapter requires {env_var!r} (dry_run=False was requested)"
        )


class MiniMaxAdapter(BaseToolAdapter):
    provider = PROVIDER_NAME
    capabilities = ToolCapabilities(supports_sync=True, supports_streaming=True, requires_auth=True)

    def __init__(
        self,
        *,
        name: str,
        dry_run: bool = True,
        api_key_env_var: str = MINIMAX_API_KEY_ENV_VAR,
        base_url: str = DEFAULT_BASE_URL,
        model_name: str = DEFAULT_MODEL,
        group_id: str | None = None,
        invocation_timeout_seconds: float = 30.0,
        max_retries: int = 2,
        cost_per_call: float = 0.0,
        transport: HTTPTransport | None = None,
        recorder: ProviderRecorder | None = None,
    ) -> None:
        super().__init__(name=name)
        self.dry_run = dry_run
        self._api_key_env_var = api_key_env_var or MINIMAX_API_KEY_ENV_VAR
        self._base_url = base_url.rstrip("/")
        self._model_name = model_name
        self._group_id = group_id
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
    ) -> "MiniMaxAdapter":
        cfg = MiniMaxProviderConfig(**configuration_manager.get_provider_config("minimax"))
        dry_run = configuration_manager.get_dry_run("providers.minimax", default=True)
        return cls(
            name=name,
            dry_run=dry_run,
            api_key_env_var=cfg.api_key_env_var,
            base_url=cfg.base_url,
            model_name=cfg.model_name,
            group_id=cfg.group_id,
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
            raise MiniMaxAuthenticationError(self._api_key_env_var)

    async def health_check(self) -> bool:
        if self.dry_run:
            return True
        if not self._transport:
            return True
        try:
            response = await self._transport.send(
                HTTPRequest(
                    method="GET",
                    url=f"{self._base_url}/models",
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
            raise MiniMaxAuthenticationError(self._api_key_env_var)

        sw = Stopwatch()
        sw.start()
        try:
            body = await self._post(request, stream=False)
            elapsed_ms = sw.stop()
            data = safe_json_loads(body)
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
                provider=self.provider, tool_name=self.name, capability=capability,
                correlation_id=request.correlation_id, error=str(exc),
            )
            raise

    async def invoke_stream(self, request: ToolInvocationRequest) -> AsyncIterator[ToolStreamChunk]:
        capability = str(request.parameters.get("capability", "reasoning"))
        if self.dry_run:
            yield ToolStreamChunk(
                tool_name=request.tool_name,
                correlation_id=request.correlation_id,
                sequence=0,
                data={"delta": "[dry_run] no live MiniMax stream", "dry_run": True, "provider": self.provider},
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

        body = json.dumps(self._build_payload(request, stream=True)).encode("utf-8")
        seq = 0
        try:
            async for raw in self._transport.stream(
                HTTPRequest(
                    method="POST",
                    url=f"{self._base_url}/text/chatcompletion_v2",
                    headers={"Content-Type": "application/json", **self._headers()},
                    body=body,
                    timeout_seconds=self._timeout,
                )
            ):
                events = self._parse_sse_events(raw)
                for ev in events:
                    seq += 1
                    if ev.get("done"):
                        yield ToolStreamChunk(
                            tool_name=request.tool_name, correlation_id=request.correlation_id,
                            sequence=seq, data={"done": True}, is_final=True,
                        )
                        return
                    yield ToolStreamChunk(
                        tool_name=request.tool_name, correlation_id=request.correlation_id,
                        sequence=seq, data={"delta": ev.get("delta", "")},
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
                "note": "dry_run=True: no live MiniMax call was made.",
            },
        )

    def _headers(self) -> dict[str, str]:
        h = make_authorization_header(self._load_api_key() or "")
        if self._group_id:
            h["X-Group-Id"] = self._group_id
        return h

    def _build_payload(self, request: ToolInvocationRequest, *, stream: bool) -> dict[str, Any]:
        params = dict(request.parameters)
        params.pop("capability", None)
        model = params.pop("model", None) or self._model_name
        messages = params.pop("messages", None)
        if messages is None:
            prompt = params.pop("prompt", "")
            messages = [{"role": "user", "content": prompt}]
        payload: dict[str, Any] = {"model": model, "messages": messages, "stream": stream}
        for k, v in params.items():
            if v is not None:
                payload[k] = v
        return payload

    async def _post(self, request: ToolInvocationRequest, *, stream: bool) -> bytes:
        if not self._transport:
            raise RuntimeError("MiniMaxAdapter has no transport configured for production calls")
        body = json.dumps(self._build_payload(request, stream=stream)).encode("utf-8")
        response = await self._transport.send(
            HTTPRequest(
                method="POST",
                url=f"{self._base_url}/text/chatcompletion_v2",
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
                if data == "[DONE]":
                    events.append({"done": True})
                continue
            try:
                events.append(json.loads(data))
            except json.JSONDecodeError:
                continue
        return events

    @staticmethod
    def _extract_text(data: dict[str, Any]) -> str:
        choices = data.get("choices") or []
        if not choices:
            return ""
        message = (choices[0] or {}).get("message") or {}
        content = message.get("content", "")
        if isinstance(content, list):
            return "".join(part.get("text", "") for part in content if isinstance(part, dict))
        return content if isinstance(content, str) else str(content)

    @staticmethod
    def _extract_usage(data: dict[str, Any]) -> dict[str, int]:
        usage = data.get("usage") or {}
        return {
            "input_tokens": int(usage.get("prompt_tokens", 0)),
            "output_tokens": int(usage.get("completion_tokens", 0)),
            "total_tokens": int(usage.get("total_tokens", 0)),
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
    tool_name: str = "minimax",
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
    "MiniMaxAdapter",
    "MiniMaxAuthenticationError",
    "MiniMaxProviderConfig",
    "MINIMAX_API_KEY_ENV_VAR",
    "PROVIDER_NAME",
    "DEFAULT_BASE_URL",
    "DEFAULT_MODEL",
    "SUPPORTED_CAPABILITIES",
    "register_with_capability_registry",
]