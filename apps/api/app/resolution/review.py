"""Control-plane review of exception recommendations (assist mode).

The planner's verdicts are recorded wherever the agent runs - possibly a sandboxed worker - so the control plane
never relies on them. Before a recommendation is shown to an operator, and again when the operator decides, it is
re-verified here against inventory read fresh from the airline system and the policies retrieved for the plan.
Waivers additionally need an operator who is a configured duty manager.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx

from app.approval.gateway import ApprovalError, ApprovalGateway
from app.db.models import RebookingPlan
from app.domain.enums import AssignmentStatus, Cabin
from app.domain.models import AffectedPassengerDTO, Assignment, FlightDTO, OptimizationRequest, Penalties, PolicyHit
from app.rag.compiler import compile_policy_rules
from app.resolution.models import DecisionKind, ExceptionDecision, ExceptionProposal, VerifiedProposal
from app.resolution.verifier import EXCEPTION_STATUSES, WAIVER_ROLE, verify_proposals


class ExceptionReviewError(ApprovalError):
    code = "EXCEPTION_REVIEW"

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.details = details or {}


class WaiverAuthorityError(ApprovalError):
    code = "WAIVER_AUTHORITY"


class ResolutionReviewer:
    def __init__(
        self,
        gateway: ApprovalGateway,
        airline_base_url: str,
        transport: Callable[[], httpx.AsyncBaseTransport | None],
        *,
        duty_managers: set[str],
        mode: str,
        timeout: float = 30.0,
    ) -> None:
        self.gateway = gateway
        self.airline_base_url = airline_base_url.rstrip("/")
        self.transport = transport  # resolved per call: tests route the airline API in-process
        self.duty_managers = duty_managers
        self.mode = mode
        self.timeout = timeout

    # ------------------------------------------------------------------ inputs, fresh from the systems of record
    async def _context(self, plan: RebookingPlan) -> tuple[OptimizationRequest, list[Assignment], set[str]]:
        async with httpx.AsyncClient(base_url=self.airline_base_url, transport=self.transport(), timeout=self.timeout) as h:
            flight = FlightDTO.model_validate((await h.get(f"/api/flights/{plan.flight_no}")).raise_for_status().json())
            pax = (await h.get(f"/api/flights/{plan.flight_no}/passengers")).raise_for_status().json()["passengers"]
            alts = (
                (
                    await h.get(
                        "/api/flights/alternatives",
                        params={
                            "origin": flight.origin,
                            "destination": flight.destination,
                            "departure_date": flight.departure_time.date().isoformat(),
                            "exclude": flight.flight_no,
                        },
                    )
                )
                .raise_for_status()
                .json()["flights"]
            )
        hits = [PolicyHit.model_validate(h) for h in plan.policy_evidence]
        req = OptimizationRequest(
            disrupted_flight=flight,
            passengers=[AffectedPassengerDTO.model_validate(p) for p in pax],
            alternatives=[FlightDTO.model_validate(f) for f in alts],
            rules=compile_policy_rules(hits),
        )
        return req, [_assignment(i) for i in plan.items], {h.policy_id for h in hits}

    # ------------------------------------------------------------------ what the operator is shown
    async def recommendations(self, plan_id: str) -> list[dict[str, Any]]:
        """The planner's standing recommendations, each re-verified here (jointly, as they would be approved)."""
        plan = self.gateway.get_plan(plan_id)
        finals = [r for r in self.gateway.get_exception_resolutions(plan_id) if r.final]
        if not finals:
            return []
        req, assignments, known = await self._context(plan)
        verified = verify_proposals([ExceptionProposal.model_validate(r.proposal) for r in finals], req, assignments, known)
        items = {i.passenger_id: i.id for i in plan.items}
        return [
            {
                **_verified_view(v),
                "item_id": items.get(r.passenger_id),
                "planner": r.planner,
                "prompt_version": r.prompt_version,
                "planner_verdict": r.verdict,
            }
            for r, v in zip(finals, verified, strict=True)
        ]

    # ------------------------------------------------------------------ what the operator decides
    async def review(self, plan_id: str, operator: str, decisions: list[ExceptionDecision]) -> list[dict[str, Any]]:
        """Resolve decisions to proposals, verify them together, check waiver authority. Returns what to store."""
        if not decisions:
            return []
        if self.mode != "assist":
            raise ExceptionReviewError(f"exception decisions need EXCEPTION_RESOLUTION_MODE=assist (now {self.mode})")
        plan = self.gateway.get_plan(plan_id)
        items = {i.passenger_id: i for i in plan.items}
        finals = {r.passenger_id: r for r in self.gateway.get_exception_resolutions(plan_id) if r.final}

        chosen: list[tuple[ExceptionDecision, ExceptionProposal | None, str]] = []
        seen: set[str] = set()
        for d in decisions:
            pid = d.passenger_id.strip().upper()
            item = items.get(pid)
            if item is None or AssignmentStatus(item.status) not in EXCEPTION_STATUSES:
                raise ExceptionReviewError(f"{pid} is not an exception passenger of plan {plan_id}")
            if pid in seen:
                raise ExceptionReviewError(f"more than one decision for {pid}")
            seen.add(pid)
            if d.decision == DecisionKind.ACCEPT:
                if pid not in finals:
                    raise ExceptionReviewError(f"there is no recommendation for {pid} to accept")
                chosen.append((d, ExceptionProposal.model_validate(finals[pid].proposal), "planner"))
            elif d.decision == DecisionKind.MODIFY:
                if d.proposal is None or d.proposal.passenger_id.strip().upper() != pid:
                    raise ExceptionReviewError(f"MODIFY for {pid} needs the operator's proposal for {pid}")
                chosen.append((d, d.proposal, "operator"))
            else:
                chosen.append((d, None, "operator"))

        to_verify = [p for _, p, _ in chosen if p is not None]
        verified: dict[str, VerifiedProposal] = {}
        if to_verify:
            req, assignments, known = await self._context(plan)
            verified = {v.proposal.passenger_id: v for v in verify_proposals(to_verify, req, assignments, known)}
        failures = {pid: [f"{x.code.value}: {x.message}" for x in v.violations] for pid, v in verified.items() if not v.accepted}
        if failures:
            raise ExceptionReviewError("exception decisions failed verification - nothing was approved", failures)
        waivers = sorted(pid for pid, v in verified.items() if v.required_role == WAIVER_ROLE)
        if waivers and operator not in self.duty_managers:
            raise WaiverAuthorityError(f"policy waivers for {', '.join(waivers)} must be approved by a duty manager")

        stored = []
        for d, _, source in chosen:
            pid = d.passenger_id.strip().upper()
            v = verified.get(pid)
            stored.append(
                {
                    "passenger_id": pid,
                    "item_id": items[pid].id,
                    "decision": d.decision.value,
                    "source": source,
                    "decided_by": operator,
                    **(_verified_view(v) if v else _NO_ACTION),
                }
            )
        return stored


# a REJECT decision: same shape as a verified one, with nothing to do
_NO_ACTION: dict[str, Any] = {
    "action": None,
    "proposal": None,
    "verdict": None,
    "violations": [],
    "required_role": None,
    "waived": [],
    "seat": None,
}


def _verified_view(v: VerifiedProposal) -> dict[str, Any]:
    p = v.proposal
    return {
        "passenger_id": p.passenger_id,
        "action": p.action.value,
        "proposal": p.model_dump(mode="json"),
        "verdict": v.verdict.value,
        "violations": [f"{x.code.value}: {x.message}" for x in v.violations],
        "required_role": v.required_role,
        "waived": v.waived,
        "seat": {"flight_no": v.seat[0], "cabin": v.seat[1].value} if v.seat else None,
    }


def _assignment(i) -> Assignment:
    """The verifier's view of a stored plan item (only allocation fields matter to it)."""
    pen = i.penalties or {}
    return Assignment(
        passenger_id=i.passenger_id,
        reservation_id=i.reservation_id,
        name=i.passenger_name,
        tier=i.tier,
        vip=i.vip,
        special_assistance=pen.get("special_assistance"),
        is_connection=bool(pen.get("is_connection")),
        original_flight=i.original_flight,
        original_cabin=Cabin(i.original_cabin),
        alternative_flight=i.alternative_flight,
        new_cabin=Cabin(i.new_cabin) if i.new_cabin else None,
        delay_minutes=i.delay_minutes,
        penalties=Penalties(),
        objective_contribution=i.score,
        status=AssignmentStatus(i.status),
        reason=i.reason,
        policy_ids=i.policy_ids,
    )
