"""Proposal and execution tools. Execution is a state-changing action behind the Approval Gateway."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.approval.gateway import ApprovalRequired
from app.domain.enums import AgentState, Component
from app.tools.base import Tool, ToolContext, ToolError, ToolResult


class ProposeArgs(BaseModel):
    flight_no: str = Field(description="Disrupted flight number")


class ProposeRebooking(Tool):
    name = "propose_rebooking"
    description = (
        "Package the solver's allocation with policy evidence and an operator briefing into a recovery plan "
        "and request human approval. Does not change any booking."
    )
    Args = ProposeArgs
    state = AgentState.GENERATING_PROPOSAL
    component = Component.APPROVAL_GATEWAY

    def precondition(self, ctx: ToolContext) -> str | None:
        return None if ctx.memory.optimization else "call optimize_rebooking first"

    async def run(self, args: ProposeArgs, ctx: ToolContext) -> ToolResult:
        m = ctx.memory
        assert m.optimization is not None and m.flight is not None
        briefing = await ctx.write_briefing() if ctx.write_briefing else ""
        m.briefing = briefing
        plan, approval = ctx.gateway.create_plan(
            task_id=ctx.task_id,
            flight_no=m.flight.flight_no,
            result=m.optimization,
            policy_hits=sorted(m.policy_hits.values(), key=lambda h: h.policy_id),
            explanation=briefing,
            requested_by=ctx.agent,
        )
        m.plan_id, m.approval_id = plan.id, approval.id
        return ToolResult(
            title=f"Recovery plan {plan.id} created · approval {approval.status} (expires {approval.expires_at:%H:%M} UTC)",
            llm_view={"plan_id": plan.id, "approval_id": approval.id, "approval_status": approval.status},
            detail={"plan_id": plan.id, "approval_id": approval.id},
        )


class ExecuteArgs(BaseModel):
    plan_id: str


class ExecuteRebooking(Tool):
    name = "execute_rebooking"
    description = (
        "Re-issue bookings for an APPROVED recovery plan. Requires prior operator approval; calls without "
        "approval are rejected by the Approval Gateway and recorded in the audit log."
    )
    Args = ExecuteArgs
    state = AgentState.EXECUTING
    component = Component.APPROVAL_GATEWAY
    mutating = True
    requires_approval = True

    async def run(self, args: ExecuteArgs, ctx: ToolContext) -> ToolResult:
        try:
            grant = ctx.gateway.authorize_execution(args.plan_id)
        except ApprovalRequired:
            ctx.http.audit.record(
                agent=ctx.agent,
                tool=self.name,
                target=f"plan:{args.plan_id}",
                action="booking.execute",
                policy="approval-gateway",
                result="DENY",
                enforced_by="approval-gateway",
                task_id=ctx.task_id,
                details={"reason": "no valid operator approval"},
            )
            raise
        ctx.http.audit.record(
            agent=ctx.agent,
            tool=self.name,
            target=f"plan:{args.plan_id}",
            action="booking.execute",
            policy="approval-gateway",
            result="ALLOW",
            enforced_by="approval-gateway",
            task_id=ctx.task_id,
            details={"approved_by": grant.approved_by, "approval_id": grant.approval_id, "items": len(grant.items)},
        )
        wire = [{k: v for k, v in it.items() if k != "item_id"} for it in grant.items]
        r = await ctx.http.request(
            "POST",
            "/api/bookings/rebookings",
            service="airline-service",
            tool=self.name,
            task_id=ctx.task_id,
            headers={"X-Approval-Token": grant.token},
            json={"plan_id": args.plan_id, "items": wire},
        )
        if r.status_code != 200:
            raise ToolError(f"booking API rejected the request ({r.status_code}): {r.text[:300]}")
        results = r.json()["results"]
        plan = ctx.gateway.record_execution(args.plan_id, results)
        ok = sum(1 for x in results if x["status"] == "CONFIRMED")
        ctx.http.audit.record(
            agent=ctx.agent,
            tool=self.name,
            target=f"airline-service:/api/bookings/rebookings plan:{args.plan_id}",
            action="booking.rebook",
            policy="RBK-001",
            result="SUCCESS" if ok == len(results) else "FAILURE",
            enforced_by="approval-gateway",
            task_id=ctx.task_id,
            details={"rebooked": ok, "failed": len(results) - ok, "approved_by": grant.approved_by},
        )
        return ToolResult(
            title=f"{ok}/{len(results)} bookings re-issued via Airline Booking API (approved by {grant.approved_by})",
            llm_view={"rebooked": ok, "failed": len(results) - ok, "plan_status": plan.status},
            detail={"results": results, "plan_status": plan.status},
        )
