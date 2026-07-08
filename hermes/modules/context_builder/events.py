"""Event-type constants the Context Builder publishes.

Namespaced `context_builder.*` (kebab-case-snake, matching the
`Standards/Event Naming Convention`). Publishing is a no-op if the
Builder was constructed without an event bus -- see `service.py`.
"""

# Successful assembly: an `AssembledContext` has been returned.
CONTEXT_BUILT = "context_builder.context.built"
# Assembly could not produce a non-empty result.
CONTEXT_BUILD_FAILED = "context_builder.context.build_failed"
