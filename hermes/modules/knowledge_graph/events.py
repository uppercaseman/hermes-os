"""Event-type constants the Knowledge Graph runtime publishes.

Namespaced `knowledge_graph.*` (kebab-case-snake, matching the
`Standards/Event Naming Convention`). Publishing is a no-op if the
graph was constructed without an event bus -- see `service.py`.

Each event fires immediately after the corresponding `KnowledgeGraph`
method completes successfully. Subscribers (Logging System via the
wildcard `*`, a future Memory Galaxy UI, future dashboards) consume
these to observe graph operations without depending on the runtime's
internals.
"""

# BFS traversal completed (neighbourhood / expansion / propagated_confidence).
KG_TRAVERSAL_PERFORMED = "knowledge_graph.traversal.performed"
# Expansion heuristic computed and returned.
KG_EXPANSION_PERFORMED = "knowledge_graph.expansion.performed"
# Influence score computed for an entry against a candidate set.
KG_INFLUENCE_COMPUTED = "knowledge_graph.influence.computed"
