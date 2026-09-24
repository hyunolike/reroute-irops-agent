"""CPU fallback solver (development / tests only): HiGHS branch-and-bound via SciPy."""

from __future__ import annotations

import asyncio
import time

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import csr_matrix

from app.optimization.base import OptimizationProvider, SolverError, SolverOutput
from app.optimization.config import SolverConfig
from app.optimization.formulation import MilpProblem


class FallbackOptimizationProvider(OptimizationProvider):
    name = "fallback"
    nvidia = False

    async def solve(self, problem: MilpProblem, config: SolverConfig) -> SolverOutput:
        return await asyncio.to_thread(self._solve, problem, config)

    def _solve(self, problem: MilpProblem, config: SolverConfig) -> SolverOutput:
        a = csr_matrix((problem.values, problem.indices, problem.offsets), shape=(problem.n_rows, problem.n_vars))
        start = time.perf_counter()
        res = milp(
            c=problem.objective,
            constraints=LinearConstraint(a, np.array(problem.row_lb), np.array(problem.row_ub)),
            integrality=np.ones(problem.n_vars),
            bounds=Bounds(0, 1),
            options={"time_limit": config.time_limit_seconds, "mip_rel_gap": config.mip_relative_gap},
        )
        elapsed = (time.perf_counter() - start) * 1000
        if res.x is None:
            raise SolverError(f"HiGHS returned no solution: {res.message}")
        status = "OPTIMAL" if res.status == 0 else "FEASIBLE"
        return SolverOutput(
            status=status,
            values=np.round(res.x),
            objective=float(res.fun),
            solve_time_ms=elapsed,
            solver="HiGHS (scipy.optimize.milp) - CPU fallback",
            raw={"message": res.message},
        )
