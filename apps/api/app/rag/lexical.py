"""Deterministic BM25 retriever (demo / fallback). Pure Python, no network."""

from __future__ import annotations

import math
import re
from collections import Counter

from app.domain.models import PolicyHit
from app.rag.base import RetrieverProvider
from app.rag.documents import PolicyChunk

_STOP = set(
    "a an the of to and or for in on at by with be is are must may should from as that this it its their "
    "any all when which than into can not no only such who".split()
)


def tokenize(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in _STOP and len(t) > 1]


class LexicalRetrieverProvider(RetrieverProvider):
    name = "lexical-bm25"
    nvidia = False

    def __init__(self, chunks: list[PolicyChunk], k1: float = 1.4, b: float = 0.75) -> None:
        super().__init__(chunks)
        self.k1, self.b = k1, b
        self.docs = [Counter(tokenize(c.search_text + " " + c.title)) for c in chunks]
        self.lengths = [sum(d.values()) for d in self.docs]
        self.avgdl = sum(self.lengths) / max(1, len(self.lengths))
        df: Counter[str] = Counter()
        for d in self.docs:
            df.update(d.keys())
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    async def search(self, query: str, top_k: int = 4) -> list[PolicyHit]:
        q = tokenize(query)
        scores: list[tuple[float, int]] = []
        for i, d in enumerate(self.docs):
            s = 0.0
            for t in q:
                if t not in d:
                    continue
                tf = d[t]
                s += self.idf[t] * tf * (self.k1 + 1) / (tf + self.k1 * (1 - self.b + self.b * self.lengths[i] / self.avgdl))
            if s > 0:
                scores.append((s, i))
        scores.sort(key=lambda x: (-x[0], self.chunks[x[1]].policy_id))
        top = scores[:top_k]
        best = top[0][0] if top else 1.0
        return [self._hit(self.chunks[i], s / best, self.name) for s, i in top]
