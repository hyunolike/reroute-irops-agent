"""Exception analysis tool: lets the reasoning model investigate WHY a passenger could not be auto-assigned
and which options exist (e.g. a policy-blocked flight that would save a connection). Read-only and advisory:
it cannot change the solver's allocation."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.domain.enums import AgentState, AssignmentStatus, Cabin, Component
from app.domain.models import OptimizationRequest
from app.optimization.formulation import screen_flights
from app.rag.compiler import compile_policy_rules
from app.tools.base import Tool, ToolContext, ToolError, ToolResult


class ExploreArgs(BaseModel):
    passenger_id: str = Field(description="Passenger id of a MANUAL_REVIEW or NO_FEASIBLE passenger, e.g. P010")


class ExploreExceptionOptions(Tool):
    name = "explore_exception_options"
    description = (
        "After optimize_rebooking: investigate one exception passenger (status MANUAL_REVIEW or NO_FEASIBLE). "
        "Returns every alternative flight with arrival delay, seats left after the plan, connection margin and the "
        "exact constraint / policy id that blocks it. Use it to recommend grounded operator actions (e.g. duty-manager "
        "endorsement, refund, re-protecting the onward flight). Advisory only - it cannot change the allocation."
    )
    Args = ExploreArgs
    state = AgentState.GENERATING_PROPOSAL
    component = Component.ORCHESTRATOR

    def precondition(self, ctx: ToolContext) -> str | None:
        return None if ctx.memory.optimization else "call optimize_rebooking first"

    async def run(self, args: ExploreArgs, ctx: ToolContext) -> ToolResult:
        m = ctx.memory
        assert m.optimization is not None and m.flight is not None
        pid = args.passenger_id.strip().upper()
        pax = next((p for p in m.passengers if p.passenger_id == pid), None)
        current = next((a for a in m.optimization.assignments if a.passenger_id == pid), None)
        if pax is None or current is None:
            raise ToolError(f"unknown passenger {pid}")
        if current.status == AssignmentStatus.AUTO_ASSIGNED:
            exceptions = [a.passenger_id for a in m.optimization.assignments if a.status != AssignmentStatus.AUTO_ASSIGNED]
            raise ToolError(f"{pid} is auto-assigned; exception passengers are {exceptions}")

        rules = compile_policy_rules(list(m.policy_hits.values()))
        req = OptimizationRequest(disrupted_flight=m.flight, passengers=[pax], alternatives=m.alternatives, rules=rules)
        _, excluded = screen_flights(req)
        blocked_flight = {e.flight_no: e for e in excluded}
        loads = m.optimization.summary.flight_loads
        options = []
        for f in m.alternatives:
            used = loads.get(f.flight_no, {})
            seats_left = {
                "business": f.business_available - used.get("business_assigned", 0),
                "economy": f.economy_available - used.get("economy_assigned", 0),
            }
            if current.alternative_flight == f.flight_no and current.new_cabin is not None:
                # the passenger's own seat in the plan is available to them
                seats_left["business" if current.new_cabin == Cabin.BUSINESS else "economy"] += 1
            blocked_by: list[str] = []
            if f.flight_no in blocked_flight:
                e = blocked_flight[f.flight_no]
                blocked_by.append(f"{e.constraint} {e.policy_id or ''}: {e.reason}".replace("  ", " "))
            if pax.special_assistance in rules.own_carrier_only_ssr and f.carrier != rules.own_carrier:
                blocked_by.append(f"C6 {rules.applied.get('ssr', '')}: {pax.special_assistance} must stay on own carrier")
            margin = None
            if pax.onward_departure_time is not None and f.destination != m.flight.destination:
                blocked_by.append(
                    f"C4: arrives {f.destination} but onward {pax.onward_flight_no} departs {m.flight.destination} (airport change)"
                )
            elif pax.onward_departure_time is not None:
                slack = int((pax.onward_departure_time - f.arrival_time).total_seconds() // 60)
                margin = slack - (rules.mct_minutes or 0)
                if margin < 0:
                    blocked_by.append(
                        f"C4 {rules.applied.get('mct', '')}: {slack} min to {pax.onward_flight_no} < MCT {rules.mct_minutes}"
                    )
            cabin_key = "business" if pax.cabin == Cabin.BUSINESS else "economy"
            if seats_left[cabin_key] <= 0 and not (pax.cabin == Cabin.BUSINESS and seats_left["economy"] > 0):
                blocked_by.append("C2: no seat left after the plan")
            options.append(
                {
                    "flight_no": f.flight_no,
                    "carrier": f.carrier,
                    "destination": f.destination,
                    "departure": f.departure_time.isoformat(),
                    "arrival": f.arrival_time.isoformat(),
                    "arrival_delay_min": int((f.arrival_time - m.flight.arrival_time).total_seconds() // 60),
                    "seats_left_after_plan": seats_left,
                    "connection_margin_min": margin,
                    "blocked_by": blocked_by,
                    "feasible_under_policy": not blocked_by,
                }
            )
        view = {
            "passenger": {
                "passenger_id": pax.passenger_id,
                "name": pax.name,
                "tier": pax.tier,
                "vip": pax.vip,
                "cabin": pax.cabin.value,
                "special_assistance": pax.special_assistance,
                "onward_flight": pax.onward_flight_no,
                "onward_departure": pax.onward_departure_time.isoformat() if pax.onward_departure_time else None,
            },
            "current_plan": {
                "status": current.status.value,
                "alternative_flight": current.alternative_flight,
                "reason": current.reason,
                "policies": current.policy_ids,
            },
            "options": options,
            "policies_available": sorted(m.policy_hits),
            "note": "Advisory only. The allocation is final solver output; recommend operator actions with policy ids.",
        }
        m.exception_analyses[pid] = view
        only_blocked_by_policy = [
            o["flight_no"]
            for o in options
            if o["blocked_by"] and all(b.startswith(("C5", "C6")) for b in o["blocked_by"])  # policy-only blocks
        ]
        title = f"Exception analysis {pid} ({current.status.value}): {sum(o['feasible_under_policy'] for o in options)} feasible option(s)"
        if only_blocked_by_policy and current.status == AssignmentStatus.NO_FEASIBLE:
            title += f" · policy-blocked but operationally possible: {', '.join(only_blocked_by_policy)}"
        return ToolResult(title=title, llm_view=view, detail=view)
