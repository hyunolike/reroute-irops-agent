"""Knowledge (policy RAG) and optimization services."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_container
from app.container import Container
from app.domain.models import OptimizationRequest, OptimizationResult
from app.optimization.base import SolverError

router = APIRouter(prefix="/api", tags=["knowledge & optimization"])


@router.get("/policies/search")
async def search_policies(
    q: str = Query(min_length=2, max_length=300), top_k: int = Query(3, ge=1, le=8), c: Container = Depends(get_container)
) -> dict:
    return await c.knowledge.search(q, top_k)


@router.get("/policies")
def list_policies(c: Container = Depends(get_container)) -> dict:
    return {
        "count": len(c.lexical.chunks),
        "policies": [
            {
                "policy_id": ch.policy_id,
                "title": ch.title,
                "source_document": ch.source_document,
                "text": ch.text,
                "params": ch.params,
            }
            for ch in c.lexical.chunks
        ],
    }


@router.post("/optimization/rebooking", response_model=OptimizationResult)
async def optimize(req: OptimizationRequest, c: Container = Depends(get_container)) -> OptimizationResult:
    try:
        return await c.optimization.optimize(req)
    except SolverError as e:
        raise HTTPException(503, f"solver unavailable: {e}") from e
