"""Exception resolution tool: the reasoning model recommends ONE action per exception passenger and a deterministic
verifier decides whether it may be shown to the operator. The tool never changes the solver's allocation and never
books anything - an accepted proposal is a recommendation that still needs human approval."""

from __future__ import annotations

from app.domain.enums import AgentState, Component
from app.domain.models import OptimizationRequest
from app.rag.compiler import compile_policy_rules
from app.resolution.models import ExceptionProposal, ResolutionAttempt, Verdict, Violation, ViolationCode
from app.resolution.verifier import unresolved_exceptions, verify_proposals
from app.tools.base import Tool, ToolContext, ToolError, ToolResult

MAX_ATTEMPTS = 2  # the first proposal plus one correction; after that the operator decides unaided


class ProposeExceptionResolution(Tool):
    name = "propose_exception_resolution"
    description = (
        "After explore_exception_options(passenger_id): recommend ONE action for that exception passenger, chosen from "
        "the options it returned. CONFIRM_SOLVER_ASSIGNMENT (MANUAL_REVIEW, with checklist) · REASSIGN_TO_OPTION "
        "(feasible_under_policy option) · REQUEST_POLICY_WAIVER (waivable_by_duty_manager option, cite the waived "
        "policy ids) · OFFER_REFUND · REROUTE_OFFLINE. A deterministic verifier returns PASS, PASS_REQUIRES_WAIVER or "
        "REJECTED with violations. Advisory only: nothing is booked and the solver's allocation is unchanged."
    )
    Args = ExceptionProposal
    state = AgentState.GENERATING_PROPOSAL
    component = Component.ORCHESTRATOR

    def precondition(self, ctx: ToolContext) -> str | None:
        if ctx.memory.optimization is None:
            return "call optimize_rebooking first"
        return "the plan was already proposed" if ctx.memory.plan_id else None

    async def run(self, args: ExceptionProposal, ctx: ToolContext) -> ToolResult:
        m = ctx.memory
        assert m.optimization is not None and m.flight is not None
        pid = args.passenger_id.strip().upper()
        if pid not in m.exception_analyses:
            raise ToolError(f"call explore_exception_options(passenger_id={pid}) first - propose only from its options")
        tries = sum(1 for a in m.resolution_attempts if a.passenger_id == pid)
        if tries >= MAX_ATTEMPTS:
            raise ToolError(f"{pid}: {tries} attempts already made - the operator decides this passenger; move on")

        rules = compile_policy_rules(list(m.policy_hits.values()))
        req = OptimizationRequest(disrupted_flight=m.flight, passengers=m.passengers, alternatives=m.alternatives, rules=rules)
        others = [v.proposal for p, v in m.resolutions.items() if p != pid]
        *prior, new = verify_proposals([*others, args], req, m.optimization.assignments, m.policy_hits.keys())
        displaced = [r.proposal.passenger_id for r in prior if not r.accepted]
        if displaced and new.accepted:
            # accepted proposals were verified together; a newcomer may not take the seats they rely on
            new.violations.append(
                Violation(code=ViolationCode.CAPACITY, message=f"takes seats already recommended for {', '.join(displaced)}")
            )
            new.verdict, new.required_role, new.waived, new.seat = Verdict.REJECTED, None, [], None

        info = ctx.planner_info() if ctx.planner_info else {}
        attempt = ResolutionAttempt(
            passenger_id=pid,
            attempt=tries + 1,
            action=new.proposal.action,
            proposal=new.proposal.model_dump(mode="json"),
            verdict=new.verdict,
            violations=new.violations,
            required_role=new.required_role,
            final=new.accepted,
            **info,
        )
        if new.accepted:
            for a in m.resolution_attempts:
                if a.passenger_id == pid:
                    a.final = False
            m.resolutions[pid] = new
        m.resolution_attempts.append(attempt)

        exhausted = {a.passenger_id for a in m.resolution_attempts} - set(m.resolutions)
        exhausted = {p for p in exhausted if sum(a.passenger_id == p for a in m.resolution_attempts) >= MAX_ATTEMPTS}
        remaining = [
            p for p in unresolved_exceptions(m.optimization.assignments, list(m.resolutions.values())) if p not in exhausted
        ]
        view = {
            "passenger_id": pid,
            "action": new.proposal.action.value,
            "verdict": new.verdict.value,
            "violations": [f"{v.code.value}: {v.message}" for v in new.violations],
            "required_role": new.required_role,
            "seat": list(new.seat) if new.seat else None,
            "waived": new.waived,
            "exceptions_without_recommendation": remaining,
        }
        if not new.accepted:
            view["next"] = (
                "correct the proposal once, using only options from explore_exception_options"
                if attempt.attempt < MAX_ATTEMPTS
                else f"no recommendation for {pid}; the operator decides this passenger"
            )
        target = f" → {new.seat[0]}" if new.seat else ""
        title = f"Resolution {pid}: {new.proposal.action.value}{target} · {new.verdict.value}"
        if new.violations:
            title += f" ({new.violations[0].code.value}: {new.violations[0].message})"
        return ToolResult(title=title, llm_view=view, detail={**view, "attempt": attempt.model_dump(mode="json")})
