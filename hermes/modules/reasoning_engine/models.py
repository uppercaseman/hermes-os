"""Pydantic data models for the Reasoning Engine.

The Reasoning Engine's role per the Sprint-3 directive is
**preparing structured `ReasoningContext` payloads for Commander**.
It is read-only over the Context Builder's output and never calls
AI models or performs provider reasoning in Sprint-3 -- that
remains out of scope and belongs to the Provider Ecosystem layer.

`ReasoningContext` is a frozen snapshot the Reasoning Engine
returns to Commander. A future Provider Ecosystem layer would
consume the same shape to dispatch an actual model call.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from hermes.modules.memory_manager.models import MemoryEntry


ReasoningMode = Literal[
    "assemble",  # default: take a Context Builder output and freeze it
    "summarize",  # future: summarize the assembled entries (Provider-driven)
    "compare",  # future: compare two contexts (Provider-driven)
]


class ReasoningRequest(BaseModel):
    """The inputs to `ReasoningEngine.prepare(...)`.

    `seed_ids` is the seed set the Context Builder expands from.
    `intent` is the natural-language description of what the
    downstream reasoning step wants to accomplish (today the
    Engine records it; a future Provider layer would consume it).
    `max_entries` caps the assembled context to the top-N entries.
    """

    requesting_agent_id: str
    seed_ids: list[uuid.UUID]
    intent: str
    mission_id: uuid.UUID | None = None
    max_entries: int = 8
    mode: ReasoningMode = "assemble"
    min_confidence: float = 0.0
    max_hops: int = 2


class ReasoningTrace(BaseModel):
    """Audit trail for one `prepare(...)` call.

    Captures the inputs, the Context Builder's assembled entries,
    and the trace data so a downstream Provider layer (or a future
    dashboard) can inspect what the Engine decided was relevant
    before any provider reasoning happened.
    """

    request_id: uuid.UUID
    request: ReasoningRequest
    context_entry_ids: list[uuid.UUID]
    context_scores: list[float]
    assembled_at: datetime
    metadata: dict[str, str] = Field(default_factory=dict)


class ReasoningContext(BaseModel):
    """The Engine's primary output -- a frozen, ordered snapshot of
    the most relevant memories for `intent`, plus the trace.

    `entries` is ordered by `context_scores` descending (the same
    ordering the Context Builder produced). The Engine adds
    `mode` and `intent` so downstream Provider consumers know
    what was being asked.
    """

    request_id: uuid.UUID
    requesting_agent_id: str
    intent: str
    mode: ReasoningMode
    mission_id: uuid.UUID | None = None
    entries: list[MemoryEntry] = Field(default_factory=list)
    context_scores: list[float] = Field(default_factory=list)
    trace: ReasoningTrace
    prepared_at: datetime