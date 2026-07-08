"""Public entry point for the Reasoning Engine.

Mirrors every other module's `interface.py`: import from here, never
from `service.py` directly. Re-exports the typed models, the
Protocol, the event constants, the factory `build_reasoning_engine`,
and the Commander `MemoryResolver` binding helper
`build_default_memory_resolver`.
"""
from __future__ import annotations

from typing import Protocol

from hermes.core.commander.models import Intent, MemoryRequirement, WorkflowPlan
from hermes.core.event_bus.interface import EventBus
from hermes.modules.reasoning_engine.contracts import ContextSource, ReasoningSink
from hermes.modules.reasoning_engine.models import (
    ReasoningContext,
    ReasoningMode,
    ReasoningRequest,
    ReasoningTrace,
)
from hermes.modules.reasoning_engine.service import ReasoningEngine

__all__ = [
    "ReasoningEngine",
    "ReasoningEngineProtocol",
    "ContextSource",
    "ReasoningSink",
    "build_reasoning_engine",
    "build_default_memory_resolver",
    "ReasoningRequest",
    "ReasoningContext",
    "ReasoningTrace",
    "ReasoningMode",
]


class ReasoningEngineProtocol(Protocol):
    """Re-export of `contracts.ReasoningEngineProtocol` at the public surface.

    The Protocol body lives in `contracts.py` so a reviewer can
    compare a real class against it without importing the implementation.
    """

    async def prepare(  # pragma: no cover - structural Protocol
        self, request: ReasoningRequest
    ) -> ReasoningContext:
        ...


def build_reasoning_engine(
    *,
    context_builder: ContextSource,
    event_bus: EventBus | None = None,
    agent_id: str = "reasoning_engine",
) -> ReasoningEngine:
    """Factory mirroring the rest of the codebase. `context_builder`
    is required (the Engine has no useful default). `event_bus` and
    `agent_id` have sensible defaults.
    """
    return ReasoningEngine(
        context_builder=context_builder,
        event_bus=event_bus,
        agent_id=agent_id,
    )


# --------------------------------------------------------------------------- #
# Commander `MemoryResolver` binding helper
# --------------------------------------------------------------------------- #


def build_default_memory_resolver(
    *,
    reasoning_engine: ReasoningEngine,
    agent_id: str | None = None,
):
    """Bind a `ReasoningEngine` to Commander's `MemoryResolver` slot.

    Commander's `MemoryResolver` Protocol declares
    `async def resolve(intent, workflow) -> MemoryRequirement`.
    The default Sprint-3 binding:

    1. Extracts seed ids from `intent.slots["seed_memory_ids"]`
       (a list of uuid strings) if present.
    2. Calls `ReasoningEngine.prepare(...)` to assemble a
       `ReasoningContext` snapshot.
    3. Translates the snapshot's ordered entries into a
       `MemoryRequirement` whose `keys` are the assembled entry
       ids (as strings) and whose `scope` mirrors the
       `MemoryScope` of the top entry.

    The binding helper **does not modify Commander service
    internals** -- it's a function Commander can call from its
    existing collaborator-wiring code. The result satisfies
    `MemoryResolver`'s shape so it can be slotted into Commander's
    collaborator bag without an interface change.

    Returns a callable with the `MemoryResolver` Protocol shape.
    """
    requester = agent_id or "commander"

    async def _resolve(intent: Intent, workflow: WorkflowPlan) -> MemoryRequirement:
        # Commander's `Intent` and `WorkflowPlan` carry data in
        # `slots` and `steps` respectively. The default binding
        # reads the seed list from `intent.slots["seed_memory_ids"]`
        # and falls back to an empty MemoryRequirement if no seed
        # list is present -- Commander treats an empty keys list as
        # "no memory requirement," so a downstream mission can still
        # execute without a context snapshot.
        seed_ids: list[str] = []
        slots = getattr(intent, "slots", None) or {}
        raw = slots.get("seed_memory_ids") if isinstance(slots, dict) else None
        if isinstance(raw, list):
            seed_ids = [s for s in raw if isinstance(s, str)]
        if not seed_ids:
            return MemoryRequirement(scope="persistent", keys=[])
        from uuid import UUID as _UUID

        parsed_seeds: list = []
        for s in seed_ids:
            try:
                parsed_seeds.append(_UUID(s))
            except (ValueError, TypeError):
                continue
        if not parsed_seeds:
            return MemoryRequirement(scope="persistent", keys=[])
        intent_text = (slots.get("description") or intent.name) if isinstance(slots, dict) else intent.name
        context = await reasoning_engine.prepare(
            ReasoningRequest(
                requesting_agent_id=requester,
                seed_ids=parsed_seeds,
                intent=intent_text,
            )
        )
        return MemoryRequirement(
            scope=context.entries[0].scope if context.entries else "persistent",
            keys=[str(e.id) for e in context.entries],
        )

    return _resolve