"""Pydantic data contracts for the Capability Registry."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ProviderHealthState = Literal["healthy", "degraded", "unavailable", "unknown"]


class CapabilityProviderRegistration(BaseModel):
    """Static, declarative ranking data for one (capability, tool_name)
    pairing -- config, not state. Dynamic state (health, observed
    latency) is tracked separately by the registry, shared across every
    capability a provider serves (see `ProviderHealth`)."""

    capability: str
    tool_name: str
    priority: int = Field(default=100, ge=0, description="Lower is preferred.")
    cost_per_call: float = Field(default=0.0, ge=0, description="Declared/estimated cost unit, provider-agnostic.")
    declared_latency_ms: float = Field(default=0.0, ge=0, description="Estimate used until real observations exist.")


class ProviderHealth(BaseModel):
    """Dynamic, runtime-tracked state for one provider (`tool_name`),
    shared across every capability it's registered for."""

    tool_name: str
    state: ProviderHealthState = "unknown"
    observed_latency_ms: float | None = None
    sample_count: int = 0
    last_error: str | None = None


class CapabilityCandidate(BaseModel):
    """One provider, scored and ready to rank, for a single `select()`
    call. `latency_ms` is the observed rolling average if any samples
    exist, otherwise the registration's declared estimate."""

    tool_name: str
    priority: int
    cost_per_call: float
    latency_ms: float
    health_state: ProviderHealthState


class CapabilitySelection(BaseModel):
    """The result of one `select()` call. `chain` is the full ordered
    fallback list -- `selected` is `chain[0].tool_name` when a selection
    was made."""

    capability: str
    selected: str | None
    chain: list[CapabilityCandidate] = Field(default_factory=list)
    overridden: bool = False
    reason: str | None = None
