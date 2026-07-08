"""Protocols and inter-module contracts for the Reasoning Engine.

The Reasoning Engine is **strictly read-only** over the Context
Builder's output. It does not call AI models, perform provider
reasoning, or write to Memory in Sprint-3 -- it prepares
structured `ReasoningContext` payloads for Commander.

The split between `ContextSource` (the Context Builder surface)
and `ReasoningSink` (a downstream provider consumer) mirrors the
separation in `Knowledge Graph.md`'s Future Considerations: today's
reasoning is preparation; a future Provider Ecosystem layer
consumes the prepared `ReasoningContext` to perform the actual
model reasoning.
"""
from __future__ import annotations

import uuid
from typing import Any, Protocol

from hermes.modules.context_builder.models import AssembledContext, ContextRequest
from hermes.modules.reasoning_engine.models import (
    ReasoningContext,
    ReasoningRequest,
)


class ContextSource(Protocol):
    """The subset of the Context Builder the Reasoning Engine consumes.

    The Engine calls `assemble` with a `ContextRequest` and reads
    the returned `AssembledContext`. It does not call any other
    Context Builder method (e.g. no event subscription, no
    write-side surface).
    """

    async def assemble(  # pragma: no cover - structural Protocol
        self, request: ContextRequest
    ) -> AssembledContext:
        ...


class ReasoningSink(Protocol):
    """The downstream consumer a prepared `ReasoningContext` is
    delivered to.

    Today, the only sink is Commander's `MemoryResolver` binding
    helper in `interface.py`. A future Provider Ecosystem layer
    would register a different sink that performs the actual
    model reasoning -- the Engine itself stays out of that path.
    """

    async def receive(  # pragma: no cover - structural Protocol
        self, context: ReasoningContext
    ) -> None:
        ...


class ReasoningEngineProtocol(Protocol):
    """The surface other modules consume.

    `prepare(...)` takes a `ReasoningRequest` and returns a
    `ReasoningContext` -- the structured payload Commander (or a
    future Provider layer) is meant to dispatch on. The Engine is
    idempotent: calling `prepare` twice with the same request
    returns equivalent contexts.
    """

    async def prepare(self, request: ReasoningRequest) -> ReasoningContext:
        ...