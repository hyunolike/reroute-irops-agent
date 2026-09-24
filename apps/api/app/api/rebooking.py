"""Recovery plans and the human approval boundary."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import get_container
from app.approval.gateway import ApprovalConflict, ApprovalError, ApprovalRequired, PlanNotFound
from app.container import Container
from app.db.models import Approval, RebookingPlan

router = APIRouter(prefix="/api/rebooking", tags=["rebooking & approval"])


def plan_view(p: RebookingPlan, a: Approval | None) -> dict:
    return {
        "id": p.id,
        "task_id": p.task_id,
        "flight_no": p.flight_no,
        "status": p.status,
        "solver": p.solver,
        "objective_value": p.objective_value,
        "summary": p.summary,
        "baseline": p.baseline,
        "policy_evidence": p.policy_evidence,
        "explanation": p.explanation,
        "execution_report": p.execution_report,
        "created_at": p.created_at.isoformat(),
        "approval": None
        if a is None
        else {
            "id": a.id,
            "status": a.status,
            "requested_by": a.requested_by,
            "approved_by": a.approved_by,
            "comment": a.comment,
            "approved_manual_item_ids": a.approved_manual_item_ids,
            "created_at": a.created_at.isoformat(),
            "approved_at": a.approved_at.isoformat() if a.approved_at else None,
            "expires_at": a.expires_at.isoformat(),
        },
        "items": [
            {
                "id": i.id,
                "passenger_id": i.passenger_id,
                "passenger_name": i.passenger_name,
                "tier": i.tier,
                "vip": i.vip,
                "reservation_id": i.reservation_id,
                "original_flight": i.original_flight,
                "alternative_flight": i.alternative_flight,
                "original_cabin": i.original_cabin,
                "new_cabin": i.new_cabin,
                "delay_minutes": i.delay_minutes,
                "score": i.score,
                "penalties": i.penalties,
                "reason": i.reason,
                "policy_ids": i.policy_ids,
                "status": i.status,
                "new_reservation_id": i.new_reservation_id,
            }
            for i in p.items
        ],
    }


def _load(c: Container, plan_id: str) -> dict:
    try:
        plan = c.gateway.get_plan(plan_id)
    except PlanNotFound as e:
        raise HTTPException(404, "plan not found") from e
    return plan_view(plan, c.gateway.get_approval(plan_id))


class Decision(BaseModel):
    comment: str | None = Field(default=None, max_length=500)
    approved_manual_item_ids: list[str] = Field(default_factory=list)


def _operator(x_operator_id: str | None) -> str:
    if not x_operator_id or not x_operator_id.strip():
        raise HTTPException(401, "X-Operator-Id header required (human operator identity)")
    return x_operator_id.strip()[:64]


@router.get("/plans/{plan_id}")
def get_plan(plan_id: str, c: Container = Depends(get_container)) -> dict:
    return _load(c, plan_id)


@router.post("/plans/{plan_id}/approve")
async def approve(
    plan_id: str, body: Decision, x_operator_id: str | None = Header(default=None), c: Container = Depends(get_container)
) -> dict:
    operator = _operator(x_operator_id)
    try:
        approval = c.gateway.approve(plan_id, operator, body.comment, body.approved_manual_item_ids)
    except PlanNotFound as e:
        raise HTTPException(404, "plan not found") from e
    except ApprovalConflict as e:
        raise HTTPException(409, str(e)) from e
    except ApprovalError as e:
        raise HTTPException(403, str(e)) from e
    c.audit.record(
        agent=operator,
        tool="approval-console",
        target=f"plan:{plan_id}",
        action="plan.approve",
        policy="RBK-001",
        result="SUCCESS",
        enforced_by="approval-gateway",
        task_id=c.gateway.get_plan(plan_id).task_id,
        details={"approval_id": approval.id, "manual_items": approval.approved_manual_item_ids},
    )
    plan = c.gateway.get_plan(plan_id)
    if c.settings.agent_execution == "remote":
        c.repo.update_task(plan.task_id, pending="resume")
    else:
        c.runner.submit(c.orchestrator.resume_after_approval(plan.task_id, plan_id))
    return _load(c, plan_id)


@router.post("/plans/{plan_id}/reject")
def reject(
    plan_id: str, body: Decision, x_operator_id: str | None = Header(default=None), c: Container = Depends(get_container)
) -> dict:
    operator = _operator(x_operator_id)
    try:
        c.gateway.reject(plan_id, operator, body.comment)
    except PlanNotFound as e:
        raise HTTPException(404, "plan not found") from e
    except ApprovalConflict as e:
        raise HTTPException(409, str(e)) from e
    plan = c.gateway.get_plan(plan_id)
    c.audit.record(
        agent=operator,
        tool="approval-console",
        target=f"plan:{plan_id}",
        action="plan.reject",
        policy="RBK-001",
        result="SUCCESS",
        enforced_by="approval-gateway",
        task_id=plan.task_id,
        details={"comment": body.comment},
    )
    c.orchestrator.on_rejected(plan.task_id, plan_id)
    return _load(c, plan_id)


@router.post("/plans/{plan_id}/execute")
async def execute(plan_id: str, c: Container = Depends(get_container)) -> dict:
    """Manual (re)execution entry point. Enforces approval in the backend - no UI-only checks."""
    try:
        plan = c.gateway.get_plan(plan_id)
    except PlanNotFound as e:
        raise HTTPException(404, "plan not found") from e
    approval = c.gateway.get_approval(plan_id)
    if approval is None or approval.status != "APPROVED":
        c.audit.record(
            agent="api-caller",
            tool="execute_rebooking",
            target=f"plan:{plan_id}",
            action="booking.execute",
            policy="approval-gateway",
            result="DENY",
            enforced_by="approval-gateway",
            task_id=plan.task_id,
            details={"reason": f"approval status {approval.status if approval else 'none'}"},
        )
        raise HTTPException(403, {"code": ApprovalRequired.code, "message": "operator approval required before execution"})
    if approval.consumed_at is not None:
        raise HTTPException(409, "plan already executed")
    await c.orchestrator.resume_after_approval(plan.task_id, plan_id)
    return _load(c, plan_id)
