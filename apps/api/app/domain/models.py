"""Pydantic contracts shared by the HTTP API, the agent tools and the optimizer."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import AssignmentStatus, Cabin


class DisruptionDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    type: str
    reason: str
    airline_fault: bool
    delay_minutes: int
    occurred_at: datetime


class FlightDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    flight_no: str
    carrier: str
    origin: str
    destination: str
    departure_time: datetime
    arrival_time: datetime
    status: str
    economy_capacity: int
    business_capacity: int
    economy_available: int = 0
    business_available: int = 0
    aircraft: str = ""
    terminal: str = ""
    disruption: DisruptionDTO | None = None

    def available(self, cabin: Cabin | str) -> int:
        return self.business_available if Cabin(cabin) == Cabin.BUSINESS else self.economy_available


class AffectedPassengerDTO(BaseModel):
    passenger_id: str
    name: str
    tier: str
    vip: bool
    special_assistance: str | None = None
    reservation_id: str
    pnr: str
    cabin: Cabin
    fare_class: str
    booked_at: datetime
    onward_flight_no: str | None = None
    onward_departure_time: datetime | None = None
    onward_destination: str | None = None

    @property
    def is_connection(self) -> bool:
        return self.onward_departure_time is not None


class PolicyHit(BaseModel):
    """A retrieved policy chunk with full provenance."""

    policy_id: str
    title: str
    source_document: str
    section: str
    text: str
    score: float
    params: dict[str, Any] = Field(default_factory=dict)
    retriever: str


class PolicyRules(BaseModel):
    """Constraint parameters compiled from *retrieved* policy documents (never from LLM memory)."""

    own_carrier: str = "KE"
    interline_partners: list[str] = Field(default_factory=list)
    allow_interline: bool = False
    max_delay_hours: float | None = None
    mct_minutes: int | None = None
    connection_risk_buffer_minutes: int = 0
    allow_coterminal: bool = False
    allow_upgrade: bool = False
    preserve_cabin: bool = False
    vip_priority: bool = False
    manual_confirmation_ssr: list[str] = Field(default_factory=list)
    own_carrier_only_ssr: list[str] = Field(default_factory=list)
    rebooking_threshold_delay_minutes: int | None = None
    meal_voucher_delay_minutes: int | None = None
    requires_human_approval: bool = True  # informational; the Approval Gateway enforces it regardless
    applied: dict[str, str] = Field(default_factory=dict, description="rule name -> policy id")
    missing: list[str] = Field(default_factory=list, description="rules with no retrieved policy")


class OptimizationRequest(BaseModel):
    disrupted_flight: FlightDTO
    passengers: list[AffectedPassengerDTO]
    alternatives: list[FlightDTO]
    rules: PolicyRules


class Penalties(BaseModel):
    delay: float = 0.0
    vip_delay: float = 0.0
    downgrade: float = 0.0
    connection_risk: float = 0.0
    rebooking_cost: float = 0.0
    unassigned: float = 0.0

    @property
    def total(self) -> float:
        return self.delay + self.vip_delay + self.downgrade + self.connection_risk + self.rebooking_cost + self.unassigned


class Assignment(BaseModel):
    passenger_id: str
    reservation_id: str
    name: str
    tier: str
    vip: bool
    special_assistance: str | None
    is_connection: bool
    original_flight: str
    original_cabin: Cabin
    alternative_flight: str | None
    new_cabin: Cabin | None
    new_departure_time: datetime | None = None
    new_arrival_time: datetime | None = None
    delay_minutes: int | None
    connection_margin_minutes: int | None = None
    penalties: Penalties
    objective_contribution: float
    status: AssignmentStatus
    reason: str
    policy_ids: list[str] = Field(default_factory=list)


class ExcludedFlight(BaseModel):
    flight_no: str
    reason: str
    constraint: str
    policy_id: str | None = None


class BaselineComparison(BaseModel):
    method: str
    accommodated: int
    policy_violations: int
    missed_connections: int
    ssr_violations: int
    business_downgrades: int
    avg_delay_minutes: float
    vip_avg_delay_minutes: float


class OptimizationSummary(BaseModel):
    affected: int
    assigned: int
    auto_assigned: int
    manual_review: int
    no_feasible: int
    business_downgrades: int
    connections_protected: int
    avg_delay_minutes: float
    vip_avg_delay_minutes: float
    flight_loads: dict[str, dict[str, int]]


class OptimizationResult(BaseModel):
    provider: str
    solver: str
    nvidia: bool
    status: str
    objective_value: float
    solve_time_ms: float
    variables: int
    constraints: int
    assignments: list[Assignment]
    excluded_flights: list[ExcludedFlight]
    summary: OptimizationSummary
    baseline: BaselineComparison
    weights: dict[str, Any]
    notes: list[str] = Field(default_factory=list)
