"""Hard constraints shared by the MILP formulation, the exception analysis tool and the exception verifier.

One definition of "is flight f allowed for passenger p", so the solver, the options shown to the reasoning
model and the verifier that checks the model's proposals can never disagree on feasibility.

Every block records which compiled policy rule caused it. Only blocks raised by a *commercial* policy rule
(co-terminal, interline, re-protection window) can be waived by a duty manager; operational and safety blocks
(flight not operating, MCT, airport change, SSR own-metal, seat capacity) never can.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.enums import Cabin
from app.domain.models import AffectedPassengerDTO, ExcludedFlight, FlightDTO, OptimizationRequest, PolicyRules

WAIVABLE_RULES = frozenset({"coterminal", "interline", "max_delay"})


@dataclass(frozen=True)
class Block:
    constraint: str  # C2 / C4 / C5 / C6 (see formulation.py)
    reason: str
    policy_id: str | None = None
    rule: str | None = None  # compiled rule key (rag/compiler.py) that raised it; None = operational fact

    @property
    def waivable(self) -> bool:
        return self.rule in WAIVABLE_RULES

    def describe(self) -> str:
        head = f"{self.constraint} {self.policy_id}" if self.policy_id else self.constraint
        return f"{head}: {self.reason}"

    def to_excluded(self, flight_no: str) -> ExcludedFlight:
        return ExcludedFlight(flight_no=flight_no, reason=self.reason, constraint=self.constraint, policy_id=self.policy_id)


def minutes(delta_seconds: float) -> int:
    return int(round(delta_seconds / 60.0))


def flight_blocks(f: FlightDTO, req: OptimizationRequest) -> list[Block]:
    """Flight-level constraints (C5, C6), in the order the formulation reports them."""
    rules = req.rules
    orig = req.disrupted_flight
    if f.flight_no == orig.flight_no or f.status == "CANCELLED":
        return [Block("C6", "flight not operating")]
    if f.status != "SCHEDULED":
        # an operational fact, even though the re-protection policy is cited for context
        return [Block("C6", f"flight is itself disrupted ({f.status})", rules.applied.get("max_delay"))]
    blocks: list[Block] = []
    if f.destination != orig.destination and not rules.allow_coterminal:
        blocks.append(
            Block(
                "C5",
                f"destination {f.destination} != {orig.destination} (co-terminal not allowed)",
                rules.applied.get("coterminal"),
                "coterminal",
            )
        )
    if f.carrier != rules.own_carrier and (not rules.allow_interline or f.carrier not in rules.interline_partners):
        blocks.append(Block("C6", f"carrier {f.carrier} has no interline agreement", rules.applied.get("interline"), "interline"))
    if rules.max_delay_hours is not None:
        dep_delay_h = (f.departure_time - orig.departure_time).total_seconds() / 3600
        if dep_delay_h > rules.max_delay_hours:
            blocks.append(
                Block(
                    "C6",
                    f"departs {dep_delay_h:.1f}h after original (> {rules.max_delay_hours:g}h limit)",
                    rules.applied.get("max_delay"),
                    "max_delay",
                )
            )
    if f.departure_time <= orig.departure_time:
        blocks.append(Block("C6", "departs before disruption"))
    return blocks


def passenger_blocks(p: AffectedPassengerDTO, f: FlightDTO, req: OptimizationRequest) -> tuple[list[Block], int | None]:
    """Passenger-level constraints (C4, C6-SSR). Returns the blocks and the connection margin over MCT."""
    rules = req.rules
    orig = req.disrupted_flight
    blocks: list[Block] = []
    if p.special_assistance in rules.own_carrier_only_ssr and f.carrier != rules.own_carrier:
        blocks.append(Block("C6", f"{p.special_assistance} must stay on own carrier", rules.applied.get("ssr"), "ssr"))
    margin: int | None = None
    if p.onward_departure_time is not None:
        if f.destination != orig.destination:
            blocks.append(
                Block(
                    "C4",
                    f"arrives {f.destination} but onward {p.onward_flight_no} departs {orig.destination} (airport change)",
                )
            )
        else:
            slack = minutes((p.onward_departure_time - f.arrival_time).total_seconds())
            mct = rules.mct_minutes or 0
            margin = slack - mct
            if margin < 0:
                blocks.append(Block("C4", f"{slack} min to {p.onward_flight_no} < MCT {mct}", rules.applied.get("mct"), "mct"))
    return blocks, margin


def eligible_cabins(p: AffectedPassengerDTO, rules: PolicyRules) -> list[Cabin]:
    """Business may be downgraded (penalised, C3); economy is upgraded only when policy allows it."""
    if p.cabin == Cabin.BUSINESS:
        return [Cabin.BUSINESS, Cabin.ECONOMY]
    return [Cabin.ECONOMY, Cabin.BUSINESS] if rules.allow_upgrade else [Cabin.ECONOMY]
