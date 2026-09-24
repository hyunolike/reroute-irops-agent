"""Policy retrieval tool (RAG over airline policy documents via the ReRoute knowledge service)."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.domain.enums import AgentState, Component
from app.domain.models import PolicyHit
from app.tools.base import Tool, ToolContext, ToolResult


class PolicyArgs(BaseModel):
    query: str = Field(min_length=3, max_length=300, description="Natural-language policy question")
    top_k: int = Field(default=3, ge=1, le=8)


class SearchRebookingPolicy(Tool):
    name = "search_rebooking_policy"
    description = (
        "Search the airline's rebooking, fare, VIP, minimum-connection-time and IROPS policy documents. "
        "Returns policy id, source document, text, relevance score and machine-readable parameters. "
        "Every rule used for allocation MUST come from this tool - never from general knowledge. "
        "You may call it several times in parallel with focused queries."
    )
    Args = PolicyArgs
    state = AgentState.RETRIEVING_POLICIES
    component = Component.NEMO_RETRIEVER

    async def run(self, args: PolicyArgs, ctx: ToolContext) -> ToolResult:
        r = await ctx.http.request(
            "GET",
            "/api/policies/search",
            service="reroute-api",
            tool=self.name,
            task_id=ctx.task_id,
            params={"q": args.query, "top_k": args.top_k},
        )
        r.raise_for_status()
        body = r.json()
        hits = [PolicyHit.model_validate(h) for h in body["hits"]]
        ctx.memory.policy_queries.append(args.query)
        for h in hits:
            prev = ctx.memory.policy_hits.get(h.policy_id)
            if prev is None or h.score > prev.score:
                ctx.memory.policy_hits[h.policy_id] = h
        view = {
            "query": args.query,
            "retriever": body.get("provider"),
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
        comp = Component.NEMO_RETRIEVER if body.get("nvidia") else Component.LEXICAL_RETRIEVER
        top = ", ".join(f"{h.policy_id} ({h.score:.2f})" for h in hits) or "no match"
        return ToolResult(
            title=f"Policy search “{args.query}” → {top}",
            llm_view=view,
            detail={
                "query": args.query,
                "provider": body.get("provider"),
                "fallback": body.get("fallback_reason"),
                "hits": [h.model_dump() for h in hits],
            },
            component=comp,
        )
