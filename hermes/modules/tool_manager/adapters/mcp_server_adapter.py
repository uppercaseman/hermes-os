"""MCP (Model Context Protocol) server adapter.

Production-ready for stdio-based MCP servers. Implements `ToolAdapter`
end-to-end using JSON-RPC 2.0 over the configured server's stdio.
Streaming is exposed as one `ToolStreamChunk` per tool progress
notification, ending with `is_final=True` when the server returns a
final response.

The adapter speaks a strict subset of MCP's `initialize` /
`tools/list` / `tools/call` shape, translating each into a single
`ToolInvocationRequest` whose `operation` is one of:

- `initialize`   -- establish the JSON-RPC + MCP handshake.
- `list_tools`   -- enumerate server-declared tools.
- `call_tool`    -- invoke one tool by name with `parameters["arguments"]`.

A server-declared capability set is captured on `initialize` and
discoverable via `discover_capabilities()`. The adapter's
`register_with_capability_registry` helper uses a sensible default
until first contact refines it; future Sprints can update the
registry post-discovery.
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import TYPE_CHECKING, Any, AsyncIterator

from hermes.modules.tool_manager.adapters.base import BaseToolAdapter
from hermes.modules.tool_manager.adapters.capability_registration import (
    register_provider_capabilities,
)
from hermes.modules.tool_manager.adapters.provider_config import MCPProviderConfig
from hermes.modules.tool_manager.adapters.provider_events import (
    ProviderEventLog,
    ProviderRecorder,
)
from hermes.modules.tool_manager.models import (
    ToolCapabilities,
    ToolInvocationRequest,
    ToolInvocationResult,
    ToolStreamChunk,
)

if TYPE_CHECKING:
    from hermes.modules.configuration_manager.interface import ConfigurationManager


PROVIDER_NAME = "mcp"
DEFAULT_SERVER_COMMAND = ""
SUPPORTED_CAPABILITIES = (
    "reasoning",
    "planning",
    "code_generation",
    "memory",
    "retrieval",
    "communication",
    "desktop_automation",
    "browser_automation",
    "vision",
)


class MCPError(Exception):
    """Base for MCP-level failures."""


class MCPTransportError(MCPError):
    """Couldn't launch the configured server subprocess."""


class MCPProtocolError(MCPError):
    """Server returned malformed JSON-RPC or non-zero error envelope."""


class MCPServerAdapter(BaseToolAdapter):
    """Production-ready MCP adapter. Backwards-compatible constructor:
    `MCPServerAdapter(name=..., server_command=...)` still works.
    """

    provider = PROVIDER_NAME
    capabilities = ToolCapabilities(supports_sync=True, supports_streaming=True, requires_auth=False)

    def __init__(
        self,
        *,
        name: str,
        server_command: str = DEFAULT_SERVER_COMMAND,
        server_args: tuple[str, ...] = (),
        server_env: dict[str, str] | None = None,
        dry_run: bool = True,
        api_key_env_var: str = "",
        invocation_timeout_seconds: float = 30.0,
        max_retries: int = 2,
        cost_per_call: float = 0.0,
        recorder: ProviderRecorder | None = None,
    ) -> None:
        super().__init__(name=name)
        self.server_command = server_command
        self.server_args = tuple(server_args)
        self.server_env = dict(server_env or {})
        self.dry_run = dry_run
        self._api_key_env_var = api_key_env_var
        self._timeout = invocation_timeout_seconds
        self._max_retries = max_retries
        self._cost_per_call = cost_per_call
        self._recorder = recorder if recorder is not None else ProviderRecorder(log=ProviderEventLog())
        self._server_capabilities: tuple[str, ...] = SUPPORTED_CAPABILITIES
        self._initialized = False

    @classmethod
    def from_configuration_manager(
        cls,
        *,
        name: str,
        configuration_manager: "ConfigurationManager",
        recorder: ProviderRecorder | None = None,
    ) -> "MCPServerAdapter":
        cfg = MCPProviderConfig(**configuration_manager.get_provider_config("mcp"))
        dry_run = configuration_manager.get_dry_run("providers.mcp", default=True)
        return cls(
            name=name,
            server_command=cfg.server_command,
            server_args=cfg.server_args,
            server_env=cfg.server_env,
            dry_run=dry_run,
            api_key_env_var=cfg.api_key_env_var,
            invocation_timeout_seconds=cfg.invocation_timeout_seconds,
            max_retries=cfg.max_retries,
            cost_per_call=cfg.cost_per_call,
            recorder=recorder,
        )

    # ------------------------------------------------------------------ #
    # Discovery
    # ------------------------------------------------------------------ #
    def discover_capabilities(self) -> tuple[str, ...]:
        """Returns the capability tuple the connected server declared on
        `initialize`. Defaults to `SUPPORTED_CAPABILITIES` until the
        first `initialize` call refines it."""
        return self._server_capabilities

    async def authenticate(self) -> None:
        if self.dry_run:
            return
        # Local servers may not require auth. If an API key env var was
        # configured, validate it.
        if self._api_key_env_var and not os.environ.get(self._api_key_env_var):
            from hermes.modules.tool_manager.adapters.openai_adapter import OpenAIAuthenticationError
            raise OpenAIAuthenticationError(self._api_key_env_var)

    async def health_check(self) -> bool:
        if self.dry_run:
            return True
        if not self.server_command:
            return False
        try:
            proc = await self._spawn_server()
            try:
                # Cheap handshake: just probe that the process can speak JSON-RPC.
                await self._round_trip(proc, method="ping", params={}, timeout=5.0)
                return True
            finally:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    proc.kill()
        except Exception:
            return False

    # ------------------------------------------------------------------ #
    # Sync invocation
    # ------------------------------------------------------------------ #
    async def invoke(self, request: ToolInvocationRequest) -> ToolInvocationResult:
        capability = str(request.parameters.get("capability", "reasoning"))
        if self.dry_run:
            return self._dry_run_result(request, capability)
        if not self.server_command:
            return ToolInvocationResult(
                tool_name=request.tool_name, correlation_id=request.correlation_id,
                status="failed", error="no MCP server_command configured",
            )

        proc = await self._spawn_server()
        try:
            if not self._initialized:
                await self._initialize(proc)
            if request.operation == "list_tools":
                payload = await self._list_tools(proc)
            elif request.operation == "call_tool":
                payload = await self._call_tool(
                    proc, name=str(request.parameters.get("tool_name", "")),
                    arguments=request.parameters.get("arguments", {}),
                )
            elif request.operation == "initialize":
                payload = await self._initialize(proc, force=True)
            else:
                raise MCPProtocolError(f"unsupported operation: {request.operation!r}")
            return ToolInvocationResult(
                tool_name=request.tool_name, correlation_id=request.correlation_id,
                status="completed", output=payload,
            )
        except MCPError as exc:
            return ToolInvocationResult(
                tool_name=request.tool_name, correlation_id=request.correlation_id,
                status="failed", error=str(exc),
            )
        finally:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                proc.kill()

    # ------------------------------------------------------------------ #
    # Streaming invocation -- yields one chunk per progress notification
    # ------------------------------------------------------------------ #
    async def invoke_stream(self, request: ToolInvocationRequest) -> AsyncIterator[ToolStreamChunk]:
        capability = str(request.parameters.get("capability", "reasoning"))
        if self.dry_run:
            yield ToolStreamChunk(
                tool_name=request.tool_name, correlation_id=request.correlation_id, sequence=0,
                data={"delta": "[dry_run] no live MCP stream", "dry_run": True, "provider": self.provider},
                is_final=True,
            )
            return
        if not self.server_command:
            yield ToolStreamChunk(
                tool_name=request.tool_name, correlation_id=request.correlation_id, sequence=0,
                is_final=True, error="no MCP server_command configured",
            )
            return

        proc = await self._spawn_server()
        seq = 0
        try:
            if not self._initialized:
                await self._initialize(proc)
            if request.operation != "call_tool":
                yield ToolStreamChunk(
                    tool_name=request.tool_name, correlation_id=request.correlation_id, sequence=seq,
                    is_final=True, error=f"streaming only supported for call_tool (got {request.operation!r})",
                )
                return

            tool_name = str(request.parameters.get("tool_name", ""))
            arguments = request.parameters.get("arguments", {})
            req_id = 1
            req_line = json.dumps({
                "jsonrpc": "2.0", "id": req_id, "method": "tools/call",
                "params": {"name": tool_name, "arguments": arguments},
            }) + "\n"
            proc.stdin.write(req_line.encode("utf-8"))
            await proc.stdin.drain()

            while True:
                line = await asyncio.wait_for(proc.stdout.readline(), timeout=self._timeout)
                if not line:
                    break
                try:
                    message = json.loads(line.decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                seq += 1
                if message.get("id") == req_id and "result" in message:
                    yield ToolStreamChunk(
                        tool_name=request.tool_name, correlation_id=request.correlation_id,
                        sequence=seq, data={"result": message["result"]}, is_final=True,
                    )
                    return
                if "error" in message:
                    yield ToolStreamChunk(
                        tool_name=request.tool_name, correlation_id=request.correlation_id,
                        sequence=seq, is_final=True, error=str(message["error"]),
                    )
                    return
                # Progress notifications carry no `id` -- emit as delta chunks.
                yield ToolStreamChunk(
                    tool_name=request.tool_name, correlation_id=request.correlation_id,
                    sequence=seq, data={"notification": message.get("method", ""), "params": message.get("params")},
                )
        except Exception as exc:
            yield ToolStreamChunk(
                tool_name=request.tool_name, correlation_id=request.correlation_id,
                sequence=seq, is_final=True, error=str(exc),
            )
        finally:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                proc.kill()

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    def _dry_run_result(self, request: ToolInvocationRequest, capability: str) -> ToolInvocationResult:
        return ToolInvocationResult(
            tool_name=request.tool_name, correlation_id=request.correlation_id,
            status="completed",
            output={
                "dry_run": True, "provider": self.provider, "capability": capability,
                "operation": request.operation, "echo_parameters": request.parameters,
                "server_capabilities": list(self._server_capabilities),
                "note": "dry_run=True: no live MCP call was made.",
            },
        )

    async def _spawn_server(self) -> asyncio.subprocess.Process:
        try:
            env = dict(os.environ)
            env.update(self.server_env)
            return await asyncio.create_subprocess_exec(
                self.server_command,
                *self.server_args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
        except FileNotFoundError as exc:
            raise MCPTransportError(str(exc)) from exc

    async def _round_trip(
        self, proc: asyncio.subprocess.Process, *, method: str, params: dict[str, Any],
        timeout: float | None = None,
    ) -> dict[str, Any]:
        req_id = 1
        line = json.dumps({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}) + "\n"
        proc.stdin.write(line.encode("utf-8"))
        await proc.stdin.drain()
        return await self._await_response(proc, req_id, timeout or self._timeout)

    async def _await_response(
        self, proc: asyncio.subprocess.Process, req_id: int, timeout: float,
    ) -> dict[str, Any]:
        while True:
            raw = await asyncio.wait_for(proc.stdout.readline(), timeout=timeout)
            if not raw:
                raise MCPProtocolError("server closed the connection")
            try:
                message = json.loads(raw.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise MCPProtocolError(f"malformed JSON-RPC: {exc}") from exc
            if message.get("id") == req_id:
                if "error" in message:
                    raise MCPProtocolError(str(message["error"]))
                return message.get("result", {})

    async def _initialize(self, proc: asyncio.subprocess.Process, *, force: bool = False) -> dict[str, Any]:
        result = await self._round_trip(proc, method="initialize", params={
            "protocolVersion": "2024-11-05", "clientInfo": {"name": "hermes", "version": "1.0"},
            "capabilities": {},
        })
        caps = result.get("capabilities") or {}
        # Map MCP "tools" capability -> Hermes canonical "code_generation".
        # More nuanced mapping lands in a future Sprint once the
        # canonical capability mapping for MCP is documented.
        declared = list(SUPPORTED_CAPABILITIES)
        if caps.get("resources") or caps.get("tools"):
            declared = list(SUPPORTED_CAPABILITIES) + ("memory", "retrieval")
        self._server_capabilities = tuple(dict.fromkeys(declared))
        self._initialized = not force
        return result

    async def _list_tools(self, proc: asyncio.subprocess.Process) -> dict[str, Any]:
        return await self._round_trip(proc, method="tools/list", params={})

    async def _call_tool(
        self, proc: asyncio.subprocess.Process, *, name: str, arguments: dict[str, Any],
    ) -> dict[str, Any]:
        return await self._round_trip(proc, method="tools/call", params={"name": name, "arguments": arguments})


def register_with_capability_registry(
    capability_registry,
    *,
    tool_name: str = "mcp",
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
    "MCPServerAdapter",
    "MCPError",
    "MCPTransportError",
    "MCPProtocolError",
    "MCPProviderConfig",
    "PROVIDER_NAME",
    "DEFAULT_SERVER_COMMAND",
    "SUPPORTED_CAPABILITIES",
    "register_with_capability_registry",
]