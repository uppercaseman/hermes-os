"""Context Builder.

The Context Builder assembles the most relevant memories for any
mission or reasoning request by combining Knowledge Graph traversal,
expansion, and confidence propagation. It's the layer the
Reasoning Engine consumes to prepare its structured `ReasoningContext`.

Read-only over Memory. The single writer to Memory is the
Reflection Engine; this module never promotes entries.

Public surface mirrors every other `hermes/modules/` package:
import from here, never from `service.py` directly.

  >>> from hermes.modules.context_builder import (
  ...     ContextBuilder,
  ...     ContextBuilderProtocol,
  ...     build_context_builder,
  ...     ContextRequest,
  ...     AssembledContext,
  ...     ContextScoreEntry,
  ... )
"""
from hermes.modules.context_builder import events as events_module
from hermes.modules.context_builder.contracts import ContextBuilderProtocol, GraphReader
from hermes.modules.context_builder.errors import (
    ContextBuilderConfigError,
    ContextBuilderError,
    EmptyContextError,
)
from hermes.modules.context_builder.interface import build_context_builder
from hermes.modules.context_builder.models import (
    AssembledContext,
    ContextRequest,
    ContextScoreEntry,
)
from hermes.modules.context_builder.service import ContextBuilder

CONTEXT_BUILT = events_module.CONTEXT_BUILT
CONTEXT_BUILD_FAILED = events_module.CONTEXT_BUILD_FAILED

__all__ = [
    "ContextBuilder",
    "ContextBuilderProtocol",
    "GraphReader",
    "build_context_builder",
    "ContextRequest",
    "AssembledContext",
    "ContextScoreEntry",
    "ContextBuilderError",
    "ContextBuilderConfigError",
    "EmptyContextError",
    "CONTEXT_BUILT",
    "CONTEXT_BUILD_FAILED",
    "events_module",
]