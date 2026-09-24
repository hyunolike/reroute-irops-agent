from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class RebookingCost(BaseModel):
    own_carrier: float = 20.0
    interline: float = 150.0


class Weights(BaseModel):
    delay_weight: float = 1.0
    vip_delay_weight: float = 3.0
    tier_delay_multiplier: dict[str, float] = Field(
        default_factory=lambda: {"PLATINUM": 1.5, "GOLD": 1.25, "SILVER": 1.1, "BASIC": 1.0}
    )
    downgrade_weight: float = 400.0
    vip_downgrade_multiplier: float = 3.0
    connection_risk_weight: float = 6.0
    unassigned_weight: float = 100_000.0
    vip_unassigned_multiplier: float = 2.0
    rebooking_cost: RebookingCost = Field(default_factory=RebookingCost)


class SolverConfig(BaseModel):
    time_limit_seconds: float = 10.0
    mip_relative_gap: float = 0.0


class OptimizationConfig(BaseModel):
    weights: Weights = Field(default_factory=Weights)
    solver: SolverConfig = Field(default_factory=SolverConfig)

    @classmethod
    def load(cls, path: Path | None) -> OptimizationConfig:
        if path and path.exists():
            return cls.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")) or {})
        return cls()
