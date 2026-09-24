from __future__ import annotations

from abc import ABC, abstractmethod

from app.domain.models import PolicyHit
from app.rag.documents import PolicyChunk


class RetrieverError(RuntimeError):
    pass


class RetrieverProvider(ABC):
    """Port for policy retrieval. NVIDIA NeMo Retriever NIMs are the production adapter."""

    name: str
    nvidia: bool

    def __init__(self, chunks: list[PolicyChunk]) -> None:
        self.chunks = chunks

    @abstractmethod
    async def search(self, query: str, top_k: int = 4) -> list[PolicyHit]: ...

    def _hit(self, chunk: PolicyChunk, score: float, retriever: str) -> PolicyHit:
        return PolicyHit(
            policy_id=chunk.policy_id,
            title=chunk.title,
            source_document=chunk.source_document,
            section=chunk.section,
            text=chunk.text,
            score=round(float(score), 4),
            params=chunk.params,
            retriever=retriever,
        )
