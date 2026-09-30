"""Passenger re-accommodation as a 0-1 Integer Linear Program.

Decision variables
    x[p,f,c] in {0,1}  passenger p is re-accommodated on flight f in cabin c
    y[p]     in {0,1}  passenger p is left unassigned (penalised)

Constraints
    (C1) sum_{f,c} x[p,f,c] + y[p] = 1                    each passenger gets exactly one outcome
    (C2) sum_p x[p,f,c] <= available[f,c]                 cabin capacity
    (C3) Business keeps Business when possible            -> downgrade penalty in the objective
    (C4) onward_dep - arr(f) >= MCT                       -> x not created when violated
    (C5) destination(f) == destination(original)          -> x not created when violated
    (C6) flight/carrier allowed by retrieved policy       -> x not created when violated

Objective (minimise)
    delay + VIP delay + downgrade + connection risk + rebooking cost + unassigned penalty

The problem is solver-agnostic (`MilpProblem`); NVIDIA cuOpt and the CPU fallback solve the same object.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from app.domain.enums import Cabin
from app.domain.models import AffectedPassengerDTO, ExcludedFlight, FlightDTO, OptimizationRequest, Penalties
from app.optimization.config import Weights
from app.optimization.constraints import eligible_cabins, flight_blocks, minutes, passenger_blocks


@dataclass(frozen=True)
class Candidate:
    passenger_idx: int
    flight_idx: int
    cabin: Cabin
    delay_minutes: int
    connection_margin: int | None
    penalties: Penalties


@dataclass
class MilpProblem:
    """min c^T v  s.t.  row_lb <= A v <= row_ub,  0 <= v <= 1,  v integer (binary)."""

    var_names: list[str]
    objective: np.ndarray
    # CSR constraint matrix
    offsets: list[int]
    indices: list[int]
    values: list[float]
    row_lb: list[float]
    row_ub: list[float]
    row_names: list[str]

    @property
    def n_vars(self) -> int:
        return len(self.var_names)

    @property
    def n_rows(self) -> int:
        return len(self.row_lb)


@dataclass
class Formulation:
    request: OptimizationRequest
    weights: Weights
    candidates: list[Candidate] = field(default_factory=list)
    unassigned_cost: list[float] = field(default_factory=list)
    excluded_flights: list[ExcludedFlight] = field(default_factory=list)
    # passenger_idx -> list of human-readable reasons each flight was infeasible for them
    infeasibility: dict[int, list[str]] = field(default_factory=dict)
    # passenger_idx -> policy-excluded flights that WOULD have worked (for exception handling)
    blocked_options: dict[int, list[str]] = field(default_factory=dict)
    problem: MilpProblem | None = None

    # ------------------------------------------------------------------ helpers
    @property
    def passengers(self) -> list[AffectedPassengerDTO]:
        return self.request.passengers

    @property
    def flights(self) -> list[FlightDTO]:
        return self.request.alternatives


def screen_flights(req: OptimizationRequest) -> tuple[list[int], list[ExcludedFlight]]:
    """Apply flight-level hard constraints (C5, C6). Returns eligible flight indices."""
    eligible: list[int] = []
    excluded: list[ExcludedFlight] = []
    for i, f in enumerate(req.alternatives):
        blocks = flight_blocks(f, req)
        if blocks:
            excluded.append(blocks[0].to_excluded(f.flight_no))
        else:
            eligible.append(i)
    return eligible, excluded


def build_formulation(req: OptimizationRequest, weights: Weights) -> Formulation:
    form = Formulation(request=req, weights=weights)
    rules = req.rules
    orig = req.disrupted_flight
    eligible, form.excluded_flights = screen_flights(req)

    for p_idx, p in enumerate(req.passengers):
        tier_mult = weights.tier_delay_multiplier.get(p.tier, 1.0)
        reasons: list[str] = []
        for f_idx in eligible:
            f = req.alternatives[f_idx]
            # C4 (minimum connection time) and C6 (SSR passengers restricted to own metal)
            blocked, margin = passenger_blocks(p, f, req)
            if blocked:
                reasons.append(f"{f.flight_no}: {blocked[0].reason}")
                continue
            delay = max(0, minutes((f.arrival_time - orig.arrival_time).total_seconds()))
            cabins = eligible_cabins(p, rules)
            for cabin in cabins:
                if f.available(cabin) <= 0:
                    continue
                pen = Penalties(
                    delay=weights.delay_weight * tier_mult * delay,
                    # VIP / cabin-preservation terms are activated by the retrieved policies
                    vip_delay=weights.vip_delay_weight * delay if (p.vip and rules.vip_priority) else 0.0,
                    downgrade=weights.downgrade_weight
                    * tier_mult
                    * (weights.vip_downgrade_multiplier if (p.vip and rules.vip_priority) else 1.0)
                    if (p.cabin == Cabin.BUSINESS and cabin == Cabin.ECONOMY and rules.preserve_cabin)
                    else 0.0,
                    connection_risk=weights.connection_risk_weight * max(0, rules.connection_risk_buffer_minutes - margin)
                    if margin is not None
                    else 0.0,
                    rebooking_cost=weights.rebooking_cost.own_carrier
                    if f.carrier == rules.own_carrier
                    else weights.rebooking_cost.interline,
                )
                form.candidates.append(Candidate(p_idx, f_idx, cabin, delay, margin, pen))
            if not any(f.available(c) > 0 for c in cabins):
                reasons.append(f"{f.flight_no}: no seats in eligible cabin")
        form.infeasibility[p_idx] = reasons
        if p.is_connection:
            # policy-excluded flights that would protect the connection if a duty manager waived the policy
            by_no = {f.flight_no: f for f in req.alternatives}
            for ex in form.excluded_flights:
                f = by_no[ex.flight_no]
                fb = flight_blocks(f, req)
                if all(b.waivable for b in fb) and not passenger_blocks(p, f, req)[0] and f.available(p.cabin) > 0:
                    form.blocked_options.setdefault(p_idx, []).append(
                        f"{f.flight_no} ({ex.reason}; {ex.policy_id or ex.constraint})"
                    )
        u = weights.unassigned_weight * (weights.vip_unassigned_multiplier if p.vip else 1.0)
        form.unassigned_cost.append(u)

    form.problem = _to_milp(form)
    return form


def _to_milp(form: Formulation) -> MilpProblem:
    n_x = len(form.candidates)
    n_p = len(form.passengers)
    names = [
        f"x[{form.passengers[c.passenger_idx].passenger_id},{form.flights[c.flight_idx].flight_no},{c.cabin.value[0]}]"
        for c in form.candidates
    ] + [f"y[{p.passenger_id}]" for p in form.passengers]
    obj = np.array([c.penalties.total for c in form.candidates] + form.unassigned_cost, dtype=float)

    rows: list[tuple[list[int], list[float], float, float, str]] = []
    # C1: assignment rows
    by_p: dict[int, list[int]] = {i: [] for i in range(n_p)}
    for j, c in enumerate(form.candidates):
        by_p[c.passenger_idx].append(j)
    for p_idx in range(n_p):
        idx = by_p[p_idx] + [n_x + p_idx]
        rows.append((idx, [1.0] * len(idx), 1.0, 1.0, f"assign[{form.passengers[p_idx].passenger_id}]"))
    # C2: capacity rows
    by_fc: dict[tuple[int, Cabin], list[int]] = {}
    for j, c in enumerate(form.candidates):
        by_fc.setdefault((c.flight_idx, c.cabin), []).append(j)
    for (f_idx, cabin), idx in sorted(by_fc.items(), key=lambda kv: (kv[0][0], kv[0][1].value)):
        f = form.flights[f_idx]
        rows.append((idx, [1.0] * len(idx), 0.0, float(f.available(cabin)), f"cap[{f.flight_no},{cabin.value}]"))

    offsets, indices, values, lbs, ubs, rnames = [0], [], [], [], [], []
    for idx, vals, lb, ub, name in rows:
        indices.extend(idx)
        values.extend(vals)
        offsets.append(len(indices))
        lbs.append(lb)
        ubs.append(ub)
        rnames.append(name)
    return MilpProblem(names, obj, offsets, indices, values, lbs, ubs, rnames)
