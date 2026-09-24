"""NVIDIA NeMo Retriever adapter: hosted embedding NIM + reranking NIM (build.nvidia.com).

Embeddings: POST https://integrate.api.nvidia.com/v1/embeddings
            {"model", "input": [...], "input_type": "passage"|"query", "encoding_format": "float", "truncate": "END"}
Reranking : POST https://ai.api.nvidia.com/v1/retrieval/nvidia/<model>/reranking
            {"model", "query": {"text"}, "passages": [{"text"}]}  ->  {"rankings": [{"index", "logit"}]}
"""

from __future__ import annotations

import asyncio
import math

import httpx
import numpy as np

from app.domain.models import PolicyHit
from app.rag.base import RetrieverError, RetrieverProvider
from app.rag.documents import PolicyChunk


class NvidiaRetrieverProvider(RetrieverProvider):
    name = "nemo-retriever"
    nvidia = True

    def __init__(
        self,
        chunks: list[PolicyChunk],
        api_key: str,
        embedding_url: str,
        embedding_model: str,
        rerank_url: str,
        rerank_model: str,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        super().__init__(chunks)
        self.api_key = api_key
        self.embedding_url, self.embedding_model = embedding_url, embedding_model
        self.rerank_url, self.rerank_model = rerank_url, rerank_model
        self.timeout = timeout
        self.transport = transport
        self._matrix: np.ndarray | None = None
        self._lock = asyncio.Lock()

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=self.timeout,
            transport=self.transport,
            headers={"Authorization": f"Bearer {self.api_key}", "Accept": "application/json"},
        )

    async def _embed(self, client: httpx.AsyncClient, texts: list[str], input_type: str) -> np.ndarray:
        r = await client.post(
            self.embedding_url,
            json={
                "model": self.embedding_model,
                "input": texts,
                "input_type": input_type,
                "encoding_format": "float",
                "truncate": "END",
            },
        )
        if r.status_code != 200:
            raise RetrieverError(f"embedding NIM {r.status_code}: {r.text[:200]}")
        data = sorted(r.json()["data"], key=lambda d: d["index"])
        m = np.asarray([d["embedding"] for d in data], dtype=float)
        return m / np.linalg.norm(m, axis=1, keepdims=True)

    async def _index(self, client: httpx.AsyncClient) -> np.ndarray:
        async with self._lock:
            if self._matrix is None:
                self._matrix = await self._embed(client, [c.search_text for c in self.chunks], "passage")
            return self._matrix

    async def search(self, query: str, top_k: int = 4) -> list[PolicyHit]:
        try:
            async with self._client() as client:
                matrix = await self._index(client)
                q = (await self._embed(client, [query], "query"))[0]
                sims = matrix @ q
                cand = list(np.argsort(-sims)[: max(top_k * 2, 8)])
                r = await client.post(
                    self.rerank_url,
                    json={
                        "model": self.rerank_model,
                        "query": {"text": query},
                        "passages": [{"text": self.chunks[i].search_text} for i in cand],
                    },
                )
                if r.status_code != 200:
                    raise RetrieverError(f"rerank NIM {r.status_code}: {r.text[:200]}")
                rankings = r.json()["rankings"]
        except httpx.HTTPError as e:
            raise RetrieverError(f"NeMo Retriever unreachable: {e}") from e
        hits = []
        for rk in rankings[:top_k]:
            chunk = self.chunks[cand[rk["index"]]]
            score = 1 / (1 + math.exp(-float(rk["logit"])))  # logit -> relevance probability
            hits.append(self._hit(chunk, score, f"{self.embedding_model} + {self.rerank_model}"))
        return hits
