"""Placeholder vector search provider.

Proves the shape of the "future vector search" hook without computing
any real embedding or running any real similarity search -- both methods
are intentionally unimplemented, consistent with "do not connect to live
external APIs yet."
"""
from __future__ import annotations

import uuid


class NullVectorSearchProvider:
    async def embed(self, text: str) -> list[float]:
        raise NotImplementedError("NullVectorSearchProvider is a placeholder -- no embedding model is wired up.")

    async def search(self, query_embedding: list[float], *, top_k: int) -> list[tuple[uuid.UUID, float]]:
        raise NotImplementedError("NullVectorSearchProvider is a placeholder -- no vector index is wired up.")
