from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import numpy as np

from app.optimization.config import SolverConfig
from app.optimization.formulation import MilpProblem


class SolverError(RuntimeError):
    pass


@dataclass
class SolverOutput:
    status: str  # OPTIMAL | FEASIBLE | INFEASIBLE | ERROR
    values: np.ndarray
    objective: float
    solve_time_ms: float
    solver: str
    raw: dict = field(default_factory=dict)


class OptimizationProvider(ABC):
    """Port for a MILP solver. NVIDIA cuOpt is the production adapter."""

    name: str
    nvidia: bool

    @abstractmethod
    async def solve(self, problem: MilpProblem, config: SolverConfig) -> SolverOutput: ...

    async def health(self) -> dict:
        return {"provider": self.name, "ok": True}
