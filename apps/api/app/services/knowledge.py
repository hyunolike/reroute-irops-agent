from __future__ import annotations

from typing import Any

from app.rag.base import RetrieverProvider
from app.rag.lexical import LexicalRetrieverProvider


class PolicyKnowledgeService:
    """Policy RAG service. Primary: NeMo Retriever NIMs. Fallback: local BM25 (clearly labelled)."""

    def __init__(self, primary: RetrieverProvider, fallback: LexicalRetrieverProvider) -> None:
        self.primary = primary
        self.fallback = fallback

    async def search(self, query: str, top_k: int = 3) -> dict[str, Any]:
        reason = None
        try:
            hits = await self.primary.search(query, top_k)
            provider = self.primary
        except Exception as e:  # noqa: BLE001 - demo must not break on a network hiccup
            if self.primary is self.fallback:
                raise
            reason = f"{type(e).__name__}: {e}"[:300]
            hits = await self.fallback.search(query, top_k)
            provider = self.fallback
        return {
            "query": query,
            "provider": provider.name,
            "nvidia": provider.nvidia,
            "fallback_reason": reason,
            "hits": [h.model_dump() for h in hits],
        }
