"""Obsidian vault adapter.

Production-ready. Implements `ToolAdapter` end-to-end against a local
Obsidian vault -- reads `.md` files, supports note listing and search,
and writes are explicitly out of scope in Sprint 4 (a future Sprint can
add them behind the same protocol surface without touching Tool
Manager).

The adapter is **non-streaming** and **non-auth-bearing** -- a local
filesystem read is fully consistent with what an Obsidian plugin API
would return.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, AsyncIterator

from hermes.modules.tool_manager.adapters.base import BaseToolAdapter
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


PROVIDER_NAME = "obsidian"
SUPPORTED_CAPABILITIES = ("retrieval", "memory")


class ObsidianAdapter(BaseToolAdapter):
    provider = PROVIDER_NAME
    capabilities = ToolCapabilities(supports_sync=True, supports_streaming=False, requires_auth=False)

    def __init__(
        self,
        *,
        name: str,
        vault_path: str = "",
        dry_run: bool = True,
        invocation_timeout_seconds: float = 10.0,
        recorder: ProviderRecorder | None = None,
    ) -> None:
        super().__init__(name=name)
        self.dry_run = dry_run
        self._vault_path = vault_path or os.environ.get("OBSIDIAN_VAULT_PATH", "")
        self._timeout = invocation_timeout_seconds
        self._recorder = recorder if recorder is not None else ProviderRecorder(log=ProviderEventLog())

    async def invoke(self, request: ToolInvocationRequest) -> ToolInvocationResult:
        capability = str(request.parameters.get("capability", "retrieval"))
        if self.dry_run:
            return self._dry_run_result(request, capability)
        if not self._vault_path:
            return ToolInvocationResult(
                tool_name=request.tool_name, correlation_id=request.correlation_id,
                status="failed", error="no vault_path configured",
            )

        try:
            if request.operation == "list_notes":
                payload = self._list_notes()
            elif request.operation == "read_note":
                payload = self._read_note(str(request.parameters.get("path", "")))
            elif request.operation == "search_notes":
                payload = self._search_notes(str(request.parameters.get("query", "")))
            else:
                return ToolInvocationResult(
                    tool_name=request.tool_name, correlation_id=request.correlation_id,
                    status="failed", error=f"unsupported operation: {request.operation!r}",
                )
            return ToolInvocationResult(
                tool_name=request.tool_name, correlation_id=request.correlation_id,
                status="completed", output=payload,
            )
        except Exception as exc:
            return ToolInvocationResult(
                tool_name=request.tool_name, correlation_id=request.correlation_id,
                status="failed", error=str(exc),
            )

    async def invoke_stream(self, request: ToolInvocationRequest) -> AsyncIterator[ToolStreamChunk]:
        # Non-streaming adapter: short-circuit per Tool Manager contract.
        # Implemented as an async generator that raises on first iteration.
        from hermes.modules.tool_manager.errors import UnsupportedCapabilityError
        raise UnsupportedCapabilityError(self.name, "streaming")
        yield  # pragma: no cover -- unreachable; keeps this an async generator

    # ------------------------------------------------------------------ #
    def _dry_run_result(self, request: ToolInvocationRequest, capability: str) -> ToolInvocationResult:
        return ToolInvocationResult(
            tool_name=request.tool_name, correlation_id=request.correlation_id,
            status="completed",
            output={
                "dry_run": True, "provider": self.provider, "capability": capability,
                "operation": request.operation, "echo_parameters": request.parameters,
                "note": "dry_run=True: no live vault read was performed.",
            },
        )

    def _list_notes(self) -> dict[str, Any]:
        root = Path(self._vault_path)
        if not root.exists():
            return {"notes": [], "warning": f"vault path does not exist: {root}"}
        notes = [str(p.relative_to(root)) for p in root.rglob("*.md")]
        return {"notes": notes}

    def _read_note(self, relative_path: str) -> dict[str, Any]:
        root = Path(self._vault_path)
        target = (root / relative_path).resolve()
        # Defensive: never escape the vault.
        try:
            target.relative_to(root.resolve())
        except ValueError as exc:
            raise ValueError(f"path escapes vault: {relative_path!r}") from exc
        if not target.exists():
            return {"path": relative_path, "content": ""}
        return {"path": relative_path, "content": target.read_text(encoding="utf-8")}

    def _search_notes(self, query: str) -> dict[str, Any]:
        root = Path(self._vault_path)
        hits: list[dict[str, str]] = []
        if not root.exists() or not query:
            return {"query": query, "hits": hits}
        for p in root.rglob("*.md"):
            content = p.read_text(encoding="utf-8")
            if query in content:
                hits.append({"path": str(p.relative_to(root)), "excerpt": _first_excerpt(content, query)})
        return {"query": query, "hits": hits}


def _first_excerpt(content: str, query: str, *, context: int = 64) -> str:
    idx = content.find(query)
    if idx < 0:
        return ""
    start = max(0, idx - context)
    end = min(len(content), idx + len(query) + context)
    return content[start:end]


__all__ = ["ObsidianAdapter", "PROVIDER_NAME", "SUPPORTED_CAPABILITIES"]
