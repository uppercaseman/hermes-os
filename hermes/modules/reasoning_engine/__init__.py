"""Reasoning Engine.

The Reasoning Engine prepares structured `ReasoningContext`
payloads for Commander (and a future Provider Ecosystem layer).
It does **not** call AI models or perform provider reasoning in
Sprint-3 -- that belongs to the Provider Ecosystem layer, which
is out of scope.

The Engine's job is read-only assembly + freezing: take a
`ReasoningRequest` (intent + seed set + mission), hand it to the
Context Builder, and return a frozen `ReasoningContext` that
downstream consumers can dispatch on.

  >>> from hermes.modules.reasoning_engine import (
  ...     ReasoningEngine,
  ...     ReasoningEngineProtocol,
  ...     build_reasoning_engine,
  ...     ReasoningRequest,
  ...     ReasoningContext,
  ...     ReasoningTrace,
  ...     build_default_memory_resolver,
  ... )
"""
from hermes.modules.reasoning_engine import events as events_module
from hermes.modules.reasoning_engine.contracts import (
    ContextSource,
    ReasoningEngineProtocol,
    ReasoningSink,
)
from hermes.modules.reasoning_engine.errors import (
    EmptyReasoningContextError,
    ProviderReasoningUnavailableError,
    ReasoningConfigError,
    ReasoningEngineError,
)
from hermes.modules.reasoning_engine.interface import (
    build_default_memory_resolver,
    build_reasoning_engine,
)
from hermes.modules.reasoning_engine.models import (
    ReasoningContext,
    ReasoningMode,
    ReasoningRequest,
    ReasoningTrace,
)
from hermes.modules.reasoning_engine.service import ReasoningEngine

REASONING_PREPARATION_FAILED = events_module.REASONING_PREPARATION_FAILED
REASONING_PREPARED = events_module.REASONING_PREPARED

__all__ = [
    "ReasoningEngine",
    "ReasoningEngineProtocol",
    "build_reasoning_engine",
    "build_default_memory_resolver",
    "ReasoningRequest",
    "ReasoningContext",
    "ReasoningTrace",
    "ReasoningMode",
    "ContextSource",
    "ReasoningSink",
    "ReasoningEngineError",
    "ReasoningConfigError",
    "EmptyReasoningContextError",
    "ProviderReasoningUnavailableError",
    "REASONING_PREPARED",
    "REASONING_PREPARATION_FAILED",
    "events_module",
]