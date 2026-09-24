"""Internal control-plane API for a remote agent worker (e.g. running inside an NVIDIA OpenShell sandbox).

The worker authenticates with X-Agent-Worker-Token. It can report events, create plans and ask for an
execution grant - but the grant is refused unless a human already approved the plan. There is no
endpoint here to approve or reject; those exist only on the operator API, which the sandbox policy
additionally denies via `deny_rules`.
"""

from __future__ import annotations

import hmac
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pydantic import BaseModel, Field

from app.api.agent import task_view
from app.api.deps import get_container
from app.api.governance import audit_view
from app.api.rebooking import plan_view
from app.approval.gateway import ApprovalConflict, ApprovalRequired, PlanNotFound
from app.container import Container
from app.domain.models import OptimizationResult, PolicyHit


def require_worker(x_agent_worker_token: str | None = Header(default=None), c: Container = Depends(get_container)) -> None:
    expected = c.settings.agent_worker_token
    if expected is None:
        raise HTTPException(404, "remote agent execution is not enabled")
    if not x_agent_worker_token or not hmac.compare_digest(x_agent_worker_token, expected.get_secret_value()):
        raise HTTPException(401, "invalid worker token")


router = APIRouter(prefix="/internal/agent", tags=["internal: agent worker"], dependencies=[Depends(require_worker)])


@router.get("/runtime")
def runtime(c: Container = Depends(get_container)) -> dict:
    return {"component_overrides": {k: v.value for k, v in c.orchestrator.component_overrides.items()}}


@router.post("/tasks/claim")
def claim(c: Container = Depends(get_container)):
    claimed = c.repo.claim_pending()
    if claimed is None:
        return Response(status_code=204)
    task, action = claimed
    return {"action": action, "task": task_view(task)}


@router.get("/tasks/{task_id}")
def get_task(task_id: str, c: Container = Depends(get_container)) -> dict:
    t = c.repo.get_task(task_id)
    if t is None:
        raise HTTPException(404, "task not found")
    return task_view(t)


class TaskPatch(BaseModel):
    state: str | None = None
    plan_id: str | None = None
    flight_no: str | None = None
    report: dict[str, Any] | None = None
    error: str | None = None
    runtime: dict[str, Any] | None = None


@router.patch("/tasks/{task_id}")
def patch_task(task_id: str, body: TaskPatch, c: Container = Depends(get_container)) -> dict:
    return task_view(c.repo.update_task(task_id, **body.model_dump(exclude_unset=True)))


class EventIn(BaseModel):
    type: str
    state: str
    component: str
    title: str
    detail: dict[str, Any] = Field(default_factory=dict)
    duration_ms: float | None = None


@router.post("/tasks/{task_id}/events", status_code=201)
def add_event(task_id: str, body: EventIn, c: Container = Depends(get_container)) -> dict:
    ev = c.repo.add_event(task_id, **body.model_dump())
    return {"seq": ev.seq}


class AuditIn(BaseModel):
    agent: str
    tool: str
    target: str
    action: str
    policy: str
    result: str
    enforced_by: str
    task_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


@router.post("/audit", status_code=201)
def audit(body: AuditIn, c: Container = Depends(get_container)) -> dict:
    return audit_view(c.audit.record(**body.model_dump()))


class PlanIn(BaseModel):
    task_id: str
    flight_no: str
    result: OptimizationResult
    policy_hits: list[PolicyHit]
    explanation: str
    requested_by: str


@router.post("/plans", status_code=201)
def create_plan(body: PlanIn, c: Container = Depends(get_container)) -> dict:
    plan, approval = c.gateway.create_plan(**dict(body))
    return {
        "plan": {"id": plan.id},
        "approval": {"id": approval.id, "status": approval.status, "expires_at": approval.expires_at},
    }


@router.get("/plans/{plan_id}")
def get_plan(plan_id: str, c: Container = Depends(get_container)) -> dict:
    try:
        return plan_view(c.gateway.get_plan(plan_id), c.gateway.get_approval(plan_id))
    except PlanNotFound as e:
        raise HTTPException(404, "plan not found") from e


@router.post("/plans/{plan_id}/execution-grant")
def execution_grant(plan_id: str, c: Container = Depends(get_container)) -> dict:
    """Issues a signed booking token ONLY for a plan a human approved (single use, item-bound)."""
    try:
        g = c.gateway.authorize_execution(plan_id)
    except ApprovalRequired as e:
        raise HTTPException(403, {"code": e.code, "message": str(e)}) from e
    except ApprovalConflict as e:
        raise HTTPException(409, {"code": e.code, "message": str(e)}) from e
    return {"plan_id": g.plan_id, "approval_id": g.approval_id, "approved_by": g.approved_by, "items": g.items, "token": g.token}


class ExecutionIn(BaseModel):
    results: list[dict[str, Any]]


@router.post("/plans/{plan_id}/execution")
def record_execution(plan_id: str, body: ExecutionIn, c: Container = Depends(get_container)) -> dict:
    plan = c.gateway.record_execution(plan_id, body.results)
    return {"status": plan.status}
