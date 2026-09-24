"""Builds the model, delegates to the configured solver, and decodes the solver output.

The solver output is the source of truth for allocation: nothing downstream (including the LLM)
may change which passenger goes on which flight.
"""

from __future__ import annotations

from app.domain.enums import AssignmentStatus, Cabin
from app.domain.models import (
    Assignment,
    OptimizationRequest,
    OptimizationResult,
    OptimizationSummary,
    Penalties,
)
from app.optimization.base import OptimizationProvider
from app.optimization.baseline import fcfs_baseline
from app.optimization.config import OptimizationConfig
from app.optimization.formulation import Formulation, build_formulation


class RebookingOptimizer:
    def __init__(self, provider: OptimizationProvider, config: OptimizationConfig) -> None:
        self.provider = provider
        self.config = config

    async def optimize(self, req: OptimizationRequest) -> OptimizationResult:
        form = build_formulation(req, self.config.weights)
        assert form.problem is not None
        out = await self.provider.solve(form.problem, self.config.solver)
        assignments = self._decode(form, out.values)
        return OptimizationResult(
            provider=self.provider.name,
            solver=out.solver,
            nvidia=self.provider.nvidia,
            status=out.status,
            objective_value=round(out.objective, 2),
            solve_time_ms=round(out.solve_time_ms, 1),
            variables=form.problem.n_vars,
            constraints=form.problem.n_rows,
            assignments=assignments,
            excluded_flights=form.excluded_flights,
            summary=self._summary(form, assignments),
            baseline=fcfs_baseline(req),
            weights=self.config.weights.model_dump(),
            notes=[f"missing policy for rule '{m}' - conservative default applied" for m in req.rules.missing],
        )

    # ------------------------------------------------------------------ decoding
    def _decode(self, form: Formulation, values) -> list[Assignment]:
        rules = form.request.rules
        n_x = len(form.candidates)
        chosen: dict[int, int] = {}
        for j, c in enumerate(form.candidates):
            if values[j] > 0.5:
                chosen[c.passenger_idx] = j
        out: list[Assignment] = []
        for p_idx, p in enumerate(form.passengers):
            base = dict(
                passenger_id=p.passenger_id,
                reservation_id=p.reservation_id,
                name=p.name,
                tier=p.tier,
                vip=p.vip,
                special_assistance=p.special_assistance,
                is_connection=p.is_connection,
                original_flight=form.request.disrupted_flight.flight_no,
                original_cabin=p.cabin,
            )
            if p_idx not in chosen:
                assert values[n_x + p_idx] > 0.5
                infeasible = form.infeasibility.get(p_idx, [])
                has_candidates = any(c.passenger_idx == p_idx for c in form.candidates)
                why = (
                    "all eligible seats were allocated to higher-cost-of-delay passengers"
                    if has_candidates
                    else "; ".join(infeasible) or "no eligible alternative flight"
                )
                blocked = form.blocked_options.get(p_idx) or []
                if blocked:
                    why += (
                        ". Policy-blocked option that would protect the connection: "
                        + ", ".join(blocked)
                        + " - requires duty-manager ad-hoc endorsement, otherwise offer refund/re-routing"
                    )
                ids = self._policy_ids(rules, ["mct", "interline", "max_delay", "duty_of_care"])
                out.append(
                    Assignment(
                        **base,
                        alternative_flight=None,
                        new_cabin=None,
                        delay_minutes=None,
                        penalties=Penalties(unassigned=form.unassigned_cost[p_idx]),
                        objective_contribution=form.unassigned_cost[p_idx],
                        status=AssignmentStatus.NO_FEASIBLE,
                        reason=f"No feasible alternative: {why}.",
                        policy_ids=ids,
                    )
                )
                continue
            c = form.candidates[chosen[p_idx]]
            f = form.flights[c.flight_idx]
            reasons: list[str] = [f"{f.flight_no} {c.cabin.value.lower()}, arrives +{c.delay_minutes} min"]
            keys = ["own_carrier_first"]
            status = AssignmentStatus.AUTO_ASSIGNED
            if f.carrier != rules.own_carrier:
                reasons.append(f"interline to partner {f.carrier}")
                keys.append("interline")
            if p.cabin == Cabin.BUSINESS:
                if c.cabin == Cabin.BUSINESS:
                    reasons.append("business cabin preserved")
                else:
                    reasons.append("downgraded to economy (business sold out) - fare difference refund")
                keys.append("preserve_cabin")
            if p.vip:
                reasons.append("VIP priority")
                keys.append("vip_priority")
            if c.connection_margin is not None:
                mct = rules.mct_minutes or 0
                reasons.append(f"protects {p.onward_flight_no} connection ({c.connection_margin + mct} min >= MCT {mct})")
                keys.append("mct")
                if c.connection_margin < rules.connection_risk_buffer_minutes:
                    status = AssignmentStatus.MANUAL_REVIEW
                    reasons.append(
                        f"connection margin {c.connection_margin} min < {rules.connection_risk_buffer_minutes} min "
                        "risk buffer - operator must confirm"
                    )
                    keys.append("connection_risk")
            if p.special_assistance:
                keys.append("ssr")
                if p.special_assistance in rules.manual_confirmation_ssr:
                    status = AssignmentStatus.MANUAL_REVIEW
                    reasons.append(f"{p.special_assistance} special assistance - handling must be re-confirmed")
            out.append(
                Assignment(
                    **base,
                    alternative_flight=f.flight_no,
                    new_cabin=c.cabin,
                    new_departure_time=f.departure_time,
                    new_arrival_time=f.arrival_time,
                    delay_minutes=c.delay_minutes,
                    connection_margin_minutes=c.connection_margin,
                    penalties=c.penalties,
                    objective_contribution=round(c.penalties.total, 2),
                    status=status,
                    reason="; ".join(reasons) + ".",
                    policy_ids=self._policy_ids(rules, keys),
                )
            )
        return out

    @staticmethod
    def _policy_ids(rules, keys: list[str]) -> list[str]:
        ids: list[str] = []
        for k in keys:
            pid = rules.applied.get(k)
            if pid and pid not in ids:
                ids.append(pid)
        return ids

    @staticmethod
    def _summary(form: Formulation, assignments: list[Assignment]) -> OptimizationSummary:
        assigned = [a for a in assignments if a.alternative_flight]
        delays = [a.delay_minutes or 0 for a in assigned]
        vip = [a.delay_minutes or 0 for a in assigned if a.vip]
        loads: dict[str, dict[str, int]] = {}
        for f in form.flights:
            loads[f.flight_no] = {
                "business_assigned": 0,
                "economy_assigned": 0,
                "business_available": f.business_available,
                "economy_available": f.economy_available,
            }
        for a in assigned:
            key = "business_assigned" if a.new_cabin == Cabin.BUSINESS else "economy_assigned"
            loads[a.alternative_flight][key] += 1  # type: ignore[index]
        return OptimizationSummary(
            affected=len(assignments),
            assigned=len(assigned),
            auto_assigned=sum(a.status == AssignmentStatus.AUTO_ASSIGNED for a in assignments),
            manual_review=sum(a.status == AssignmentStatus.MANUAL_REVIEW for a in assignments),
            no_feasible=sum(a.status == AssignmentStatus.NO_FEASIBLE for a in assignments),
            business_downgrades=sum(a.original_cabin == Cabin.BUSINESS and a.new_cabin == Cabin.ECONOMY for a in assigned),
            connections_protected=sum(a.is_connection for a in assigned),
            avg_delay_minutes=round(sum(delays) / len(delays), 1) if delays else 0.0,
            vip_avg_delay_minutes=round(sum(vip) / len(vip), 1) if vip else 0.0,
            flight_loads={k: v for k, v in loads.items() if v["business_assigned"] or v["economy_assigned"]},
        )
