"""Event-type constants the Reasoning Engine publishes.

Namespaced `reasoning_engine.*` (kebab-case-snake, matching the
`Standards/Event Naming Convention`). Publishing is a no-op if the
Engine was constructed without an event bus -- see `service.py`.
"""

# A `ReasoningContext` has been prepared successfully.
REASONING_PREPARED = "reasoning_engine.context.prepared"
# The Engine could not prepare a non-empty context for the request.
REASONING_PREPARATION_FAILED = "reasoning_engine.context.preparation_failed"