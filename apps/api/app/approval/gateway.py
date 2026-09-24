"""Approval Gateway - the business authorization boundary.

OpenShell answers "can this process reach that endpoint?" (technical boundary).
The Approval Gateway answers "has an accountable human authorised this business change?".
Every state-changing booking action passes through `authorize_execution`, which is enforced in the
backend (not the UI) and produces a signed token the Booking API independently verifies.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.approval.tokens import ApprovalClaims, items_digest, mint_token
from app.db.base import Database, utcnow
from app.db.models import Approval, RebookingPlan, RebookingPlanItem
from app.domain.enums import ApprovalStatus, AssignmentStatus, PlanStatus
from app.domain.models import OptimizationResult, PolicyHit


class ApprovalError(PermissionError):
    code = "APPROVAL_ERROR"


class ApprovalRequired(ApprovalError):
    code = "APPROVAL_REQUIRED"


class ApprovalConflict(ApprovalError):
    code = "APPROVAL_CONFLICT"


class PlanNotFound(LookupError):
    pass


@dataclass
class ExecutionGrant:
    plan_id: str
    approval_id: str
    approved_by: str
    items: list[dict[str, str]]
    token: str


def _aware(dt):
    from datetime import UTC

    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=UTC)


class ApprovalGateway:
    def __init__(self, db: Database, signing_secret: str, ttl_minutes: int = 30) -> None:
        self.db = db
        self.secret = signing_secret
        self.ttl = timedelta(minutes=ttl_minutes)

    # ------------------------------------------------------------------ proposal
    def create_plan(
        self,
        *,
        task_id: str,
        flight_no: str,
        result: OptimizationResult,
        policy_hits: list[PolicyHit],
        explanation: str,
        requested_by: str,
    ) -> tuple[RebookingPlan, Approval]:
        with self.db.session() as s:
            plan = RebookingPlan(
                task_id=task_id,
                flight_no=flight_no,
                status=PlanStatus.PENDING_APPROVAL.value,
                solver=result.solver,
                objective_value=result.objective_value,
                summary={
                    **result.summary.model_dump(),
                    "provider": result.provider,
                    "nvidia": result.nvidia,
                    "status": result.status,
                    "solve_time_ms": result.solve_time_ms,
                    "variables": result.variables,
                    "constraints": result.constraints,
                    "excluded_flights": [e.model_dump() for e in result.excluded_flights],
                    "notes": result.notes,
                },
                baseline=result.baseline.model_dump(),
                policy_evidence=[h.model_dump() for h in policy_hits],
                explanation=explanation,
            )
            for i, a in enumerate(result.assignments):
                plan.items.append(
                    RebookingPlanItem(
                        position=i,
                        passenger_id=a.passenger_id,
                        passenger_name=a.name,
                        tier=a.tier,
                        vip=a.vip,
                        reservation_id=a.reservation_id,
                        original_flight=a.original_flight,
                        alternative_flight=a.alternative_flight,
                        original_cabin=a.original_cabin.value,
                        new_cabin=a.new_cabin.value if a.new_cabin else None,
                        delay_minutes=a.delay_minutes,
                        score=a.objective_contribution,
                        penalties={
                            **a.penalties.model_dump(),
                            "special_assistance": a.special_assistance,
                            "is_connection": a.is_connection,
                            "connection_margin_minutes": a.connection_margin_minutes,
                        },
                        reason=a.reason,
                        policy_ids=a.policy_ids,
                        status=a.status.value,
                    )
                )
            s.add(plan)
            s.flush()
            approval = Approval(
                plan_id=plan.id,
                status=ApprovalStatus.PENDING.value,
                requested_by=requested_by,
                expires_at=utcnow() + self.ttl,
            )
            s.add(approval)
            s.commit()
            s.refresh(plan)
            s.refresh(approval)
            return plan, approval

    # ------------------------------------------------------------------ queries
    def get_plan(self, plan_id: str) -> RebookingPlan:
        with self.db.session() as s:
            plan = s.scalar(select(RebookingPlan).options(selectinload(RebookingPlan.items)).where(RebookingPlan.id == plan_id))
            if plan is None:
                raise PlanNotFound(plan_id)
            self._expire_if_needed(s, plan)
            return plan

    def get_approval(self, plan_id: str) -> Approval | None:
        with self.db.session() as s:
            return s.scalar(select(Approval).where(Approval.plan_id == plan_id).order_by(Approval.created_at.desc()))

    def _expire_if_needed(self, s, plan: RebookingPlan) -> None:
        approval = s.scalar(select(Approval).where(Approval.plan_id == plan.id).order_by(Approval.created_at.desc()))
        if approval and approval.status == ApprovalStatus.PENDING.value and _aware(approval.expires_at) < utcnow():
            approval.status = ApprovalStatus.EXPIRED.value
            plan.status = PlanStatus.EXPIRED.value
            s.commit()

    # ------------------------------------------------------------------ operator decisions
    def approve(
        self, plan_id: str, operator: str, comment: str | None = None, manual_item_ids: list[str] | None = None
    ) -> Approval:
        if not operator or operator.lower().startswith(("agent", "reroute-agent")):
            raise ApprovalError("approvals must be made by a human operator identity")
        with self.db.session() as s:
            plan = s.get(RebookingPlan, plan_id)
            if plan is None:
                raise PlanNotFound(plan_id)
            self._expire_if_needed(s, plan)
            approval = s.scalar(select(Approval).where(Approval.plan_id == plan_id).order_by(Approval.created_at.desc()))
            if approval is None or approval.status != ApprovalStatus.PENDING.value:
                raise ApprovalConflict(f"approval is {approval.status if approval else 'missing'}, not PENDING")
            valid_manual = {i.id for i in plan.items if i.status == AssignmentStatus.MANUAL_REVIEW.value and i.alternative_flight}
            chosen = [i for i in (manual_item_ids or []) if i in valid_manual]
            approval.status = ApprovalStatus.APPROVED.value
            approval.approved_by = operator
            approval.comment = comment
            approval.approved_manual_item_ids = chosen
            approval.approved_at = utcnow()
            approval.expires_at = utcnow() + self.ttl  # execution window
            plan.status = PlanStatus.APPROVED.value
            s.commit()
            s.refresh(approval)
            return approval

    def reject(self, plan_id: str, operator: str, comment: str | None = None) -> Approval:
        with self.db.session() as s:
            plan = s.get(RebookingPlan, plan_id)
            if plan is None:
                raise PlanNotFound(plan_id)
            approval = s.scalar(select(Approval).where(Approval.plan_id == plan_id).order_by(Approval.created_at.desc()))
            if approval is None or approval.status != ApprovalStatus.PENDING.value:
                raise ApprovalConflict(f"approval is {approval.status if approval else 'missing'}, not PENDING")
            approval.status = ApprovalStatus.REJECTED.value
            approval.approved_by = operator
            approval.comment = comment
            approval.approved_at = utcnow()
            plan.status = PlanStatus.REJECTED.value
            s.commit()
            s.refresh(approval)
            return approval

    # ------------------------------------------------------------------ execution authorization
    def authorize_execution(self, plan_id: str) -> ExecutionGrant:
        with self.db.session() as s:
            plan = s.scalar(select(RebookingPlan).options(selectinload(RebookingPlan.items)).where(RebookingPlan.id == plan_id))
            if plan is None:
                raise PlanNotFound(plan_id)
            approval = s.scalar(select(Approval).where(Approval.plan_id == plan_id).order_by(Approval.created_at.desc()))
            if approval is None or approval.status != ApprovalStatus.APPROVED.value:
                raise ApprovalRequired(
                    f"plan {plan_id} requires operator approval (current: {approval.status if approval else 'none'})"
                )
            if _aware(approval.expires_at) < utcnow():
                approval.status = ApprovalStatus.EXPIRED.value
                plan.status = PlanStatus.EXPIRED.value
                s.commit()
                raise ApprovalRequired("approval expired - re-approval required")
            if approval.consumed_at is not None:
                raise ApprovalConflict("approval already used - plan was executed")
            approval.consumed_at = utcnow()
            items = [
                {"reservation_id": i.reservation_id, "flight_no": i.alternative_flight, "cabin": i.new_cabin, "item_id": i.id}
                for i in plan.items
                if i.alternative_flight
                and (
                    i.status == AssignmentStatus.AUTO_ASSIGNED.value
                    or (i.status == AssignmentStatus.MANUAL_REVIEW.value and i.id in approval.approved_manual_item_ids)
                )
            ]
            s.commit()
            wire = [{k: v for k, v in it.items() if k != "item_id"} for it in items]
            claims = ApprovalClaims(
                plan_id=plan_id,
                approval_id=approval.id,
                approved_by=approval.approved_by or "",
                digest=items_digest(wire),
                exp=int(time.time() + 300),
            )
            return ExecutionGrant(plan_id, approval.id, approval.approved_by or "", items, mint_token(self.secret, claims))

    def record_execution(self, plan_id: str, results: list[dict[str, Any]]) -> RebookingPlan:
        with self.db.session() as s:
            plan = s.scalar(select(RebookingPlan).options(selectinload(RebookingPlan.items)).where(RebookingPlan.id == plan_id))
            by_res = {r["reservation_id"]: r for r in results}
            ok = failed = 0
            for item in plan.items:
                r = by_res.get(item.reservation_id)
                if r is None:
                    if item.status == AssignmentStatus.MANUAL_REVIEW.value:
                        item.status = AssignmentStatus.HELD_FOR_OPERATOR.value
                    continue
                if r.get("status") == "CONFIRMED":
                    item.status = AssignmentStatus.EXECUTED.value
                    item.new_reservation_id = r.get("new_reservation_id")
                    ok += 1
                else:
                    item.status = AssignmentStatus.EXECUTION_FAILED.value
                    item.reason = f"{item.reason} Execution failed: {r.get('error')}"
                    failed += 1
            plan.status = PlanStatus.EXECUTED.value if failed == 0 else PlanStatus.PARTIALLY_EXECUTED.value
            plan.execution_report = {"rebooked": ok, "failed": failed, "results": results, "executed_at": utcnow().isoformat()}
            s.commit()
            s.refresh(plan)
            return plan
