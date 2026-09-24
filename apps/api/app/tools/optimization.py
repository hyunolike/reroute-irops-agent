"""Optimization tool: compiles retrieved policies into constraints and delegates allocation to cuOpt."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.domain.enums import AgentState, AssignmentStatus, Component
from app.domain.models import OptimizationRequest, OptimizationResult
from app.rag.compiler import compile_policy_rules
from app.tools.base import Tool, ToolContext, ToolError, ToolResult

_SUGGESTED = {
    "own_carrier_first": "airline fault cancellation re-protection own carrier",
    "interline": "interline partner carrier agreement",
    "max_delay": "maximum re-accommodation window",
    "mct": "minimum connection time at the destination airport",
    "connection_risk": "at-risk connection margin",
    "ssr": "special assistance passengers wheelchair unaccompanied minor",
    "coterminal": "co-terminal airports",
}


class OptimizeArgs(BaseModel):
    flight_no: str = Field(description="Disrupted flight number")


class OptimizeRebooking(Tool):
    name = "optimize_rebooking"
    description = (
        "Compute the optimal passenger-to-flight/cabin allocation with a mathematical optimization solver "
        "(NVIDIA cuOpt MILP). Uses the passengers, alternative flights and retrieved policies already loaded "
        "in this task. You must NOT allocate passengers yourself - the solver output is the source of truth."
    )
    Args = OptimizeArgs
    state = AgentState.OPTIMIZING
    component = Component.CUOPT

    def precondition(self, ctx: ToolContext) -> str | None:
        m = ctx.memory
        missing = [
            n
            for n, ok in (
                ("get_disrupted_flight", m.flight),
                ("get_affected_passengers", m.passengers),
                ("search_alternative_flights", m.alternatives),
                ("search_rebooking_policy", m.policy_hits),
            )
            if not ok
        ]
        if missing:
            return f"missing inputs - call {', '.join(missing)} first"
        rules = compile_policy_rules(list(m.policy_hits.values()))
        if rules.missing:
            hints = "; ".join(f"{r}: '{_SUGGESTED.get(r, r)}'" for r in rules.missing)
            return f"policy coverage incomplete - retrieve policies for: {hints}"
        return None

    async def run(self, args: OptimizeArgs, ctx: ToolContext) -> ToolResult:
        m = ctx.memory
        assert m.flight is not None
        rules = compile_policy_rules(list(m.policy_hits.values()))
        req = OptimizationRequest(disrupted_flight=m.flight, passengers=m.passengers, alternatives=m.alternatives, rules=rules)
        r = await ctx.http.request(
            "POST",
            "/api/optimization/rebooking",
            service="reroute-api",
            tool=self.name,
            task_id=ctx.task_id,
            json=req.model_dump(mode="json"),
        )
        if r.status_code != 200:
            raise ToolError(f"optimization service error {r.status_code}: {r.text[:300]}")
        res = OptimizationResult.model_validate(r.json())
        m.optimization = res
        s = res.summary
        exceptions = [
            {"passenger_id": a.passenger_id, "status": a.status.value, "reason": a.reason, "policies": a.policy_ids}
            for a in res.assignments
            if a.status != AssignmentStatus.AUTO_ASSIGNED
        ]
        view = {
            "solver": res.solver,
            "status": res.status,
            "objective": res.objective_value,
            "summary": s.model_dump(),
            "excluded_flights": [e.model_dump() for e in res.excluded_flights],
            "exceptions": exceptions,
            "applied_policies": rules.applied,
            "note": "Allocation is final solver output; do not modify it.",
        }
        comp = Component.CUOPT if res.nvidia else Component.FALLBACK_SOLVER
        title = (
            f"{res.solver}: {res.status} in {res.solve_time_ms:.0f} ms · {res.variables} vars × {res.constraints} constraints"
            f" → {s.auto_assigned} auto · {s.manual_review} review · {s.no_feasible} no feasible"
        )
        return ToolResult(
            title=title,
            llm_view=view,
            detail={"result": res.model_dump(mode="json"), "rules": rules.model_dump()},
            component=comp,
        )
