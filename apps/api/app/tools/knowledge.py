"""Policy retrieval tool (RAG over airline policy documents via the ReRoute knowledge service)."""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic import BaseModel, Field, model_validator

from app.domain.enums import AgentState, Component
from app.domain.models import PolicyHit
from app.rag.compiler import SUGGESTED_QUERIES, compile_policy_rules
from app.tools.base import Tool, ToolContext, ToolResult


class PolicyArgs(BaseModel):
    queries: list[str] = Field(
        min_length=1,
        max_length=8,
        description="One or more focused natural-language policy questions, e.g. "
        '["minimum connection time at NRT", "interline partner carriers"]',
    )
    top_k: int = Field(default=3, ge=1, le=8, description="Hits per query")

    @model_validator(mode="before")
    @classmethod
    def _accept_single_query(cls, data: Any) -> Any:
        # Real models sometimes send {"query": "..."} or a bare string instead of a list.
        if isinstance(data, dict):
            data = dict(data)
            if "queries" not in data and "query" in data:
                data["queries"] = data.pop("query")
            if isinstance(data.get("queries"), str):
                data["queries"] = [data["queries"]]
            if isinstance(data.get("queries"), list):
                data["queries"] = [q.strip() for q in data["queries"] if isinstance(q, str) and len(q.strip()) >= 3]
        return data


class SearchRebookingPolicy(Tool):
    name = "search_rebooking_policy"
    description = (
        "Search the airline's rebooking, fare, VIP, minimum-connection-time, special-assistance and IROPS policy "
        "documents. Returns policy id, source document, text, relevance score and machine-readable parameters, plus "
        "`coverage`: which constraint rules are already grounded by retrieved policies and which are still missing "
        "(with suggested queries). Every rule used for allocation MUST come from this tool - never from general "
        "knowledge. Keep searching until coverage.missing is empty."
    )
    Args = PolicyArgs
    state = AgentState.RETRIEVING_POLICIES
    component = Component.NEMO_RETRIEVER

    async def _search(self, q: str, top_k: int, ctx: ToolContext) -> dict:
        r = await ctx.http.request(
            "GET",
            "/api/policies/search",
            service="reroute-api",
            tool=self.name,
            task_id=ctx.task_id,
            params={"q": q, "top_k": top_k},
        )
        r.raise_for_status()
        return r.json()

    async def run(self, args: PolicyArgs, ctx: ToolContext) -> ToolResult:
        bodies = await asyncio.gather(*(self._search(q, args.top_k, ctx) for q in args.queries))
        results, all_hits, providers, fallbacks = [], [], set(), set()
        for q, body in zip(args.queries, bodies, strict=True):
            hits = [PolicyHit.model_validate(h) for h in body["hits"]]
            providers.add(body.get("provider"))
            if body.get("fallback_reason"):
                fallbacks.add(body["fallback_reason"])
            ctx.memory.policy_queries.append(q)
            for h in hits:
                prev = ctx.memory.policy_hits.get(h.policy_id)
                if prev is None or h.score > prev.score:
                    ctx.memory.policy_hits[h.policy_id] = h
            all_hits += hits
            results.append(
                {
                    "query": q,
                    "hits": [
                        {
                            "policy_id": h.policy_id,
                            "title": h.title,
                            "source": h.source_document,
                            "score": h.score,
                            "text": h.text[:400],
                            "params": h.params,
                        }
                        for h in hits
                    ],
                }
            )
        rules = compile_policy_rules(list(ctx.memory.policy_hits.values()))
        coverage = {
            "grounded": rules.applied,
            "missing": rules.missing,
            "suggested_queries": [SUGGESTED_QUERIES[m] for m in rules.missing if m in SUGGESTED_QUERIES],
        }
        nvidia = any(b.get("nvidia") for b in bodies)
        comp = Component.NEMO_RETRIEVER if nvidia else Component.LEXICAL_RETRIEVER
        top = sorted({h.policy_id for h in all_hits})
        missing = f" · still missing: {', '.join(rules.missing)}" if rules.missing else " · coverage complete"
        return ToolResult(
            title=f"{len(args.queries)} policy searches → {len(top)} policies ({', '.join(top[:6])}{'…' if len(top) > 6 else ''}){missing}",
            llm_view={"results": results, "coverage": coverage},
            detail={
                "queries": args.queries,
                "provider": ", ".join(sorted(p for p in providers if p)),
                "fallback": "; ".join(sorted(fallbacks)) or None,
                "hits": [h.model_dump() for h in all_hits],
                "coverage": coverage,
            },
            component=comp,
        )
