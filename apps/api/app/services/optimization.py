from __future__ import annotations

from app.domain.models import OptimizationRequest, OptimizationResult
from app.optimization.base import SolverError
from app.optimization.service import RebookingOptimizer


class OptimizationService:
    """Primary: NVIDIA cuOpt. Optional CPU fallback - always labelled, never reported as cuOpt."""

    def __init__(self, primary: RebookingOptimizer, fallback: RebookingOptimizer | None = None) -> None:
        self.primary = primary
        self.fallback = fallback

    async def optimize(self, req: OptimizationRequest) -> OptimizationResult:
        try:
            return await self.primary.optimize(req)
        except SolverError as e:
            if self.fallback is None or self.fallback is self.primary:
                raise
            res = await self.fallback.optimize(req)
            res.notes.insert(0, f"NVIDIA cuOpt unavailable ({e}); solved with CPU fallback instead")
            return res
