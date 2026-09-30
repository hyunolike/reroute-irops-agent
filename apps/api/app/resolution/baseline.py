"""Rule-based exception proposer.

Used by the scripted planner (demo / offline mode) and as the baseline a reasoning model must beat in evaluation.
It reads only the explore_exception_options view, exactly what the model sees.
"""

from __future__ import annotations

import re
from typing import Any

from app.domain.enums import AssignmentStatus, Cabin
from app.resolution.models import ExceptionProposal, ResolutionAction

_POLICY_ID = re.compile(r"\b[A-Z]{2,5}-\d{3}\b")


def baseline_proposal(view: dict[str, Any]) -> ExceptionProposal:
    pax, plan, options = view["passenger"], view["current_plan"], view["options"]
    pid = pax["passenger_id"]
    by_delay = sorted(options, key=lambda o: o["arrival_delay_min"])

    if plan["status"] == AssignmentStatus.MANUAL_REVIEW.value and plan["alternative_flight"]:
        flight = plan["alternative_flight"]
        checklist = []
        if pax.get("special_assistance"):
            checklist.append(f"{pax['special_assistance']} handling re-confirmed with ground staff for {flight}")
        if pax.get("onward_flight"):
            margin = next((o["connection_margin_min"] for o in options if o["flight_no"] == flight), None)
            checklist.append(f"connection to {pax['onward_flight']} accepted ({margin} min over MCT)")
        return ExceptionProposal(
            passenger_id=pid,
            action=ResolutionAction.CONFIRM_SOLVER_ASSIGNMENT,
            policy_ids=plan["policies"],
            checklist=checklist or [f"manual review reason checked: {plan['reason']}"],
            rationale=f"The solver's {flight} seat is the best option; it needs an operator check, not a different flight.",
        )

    feasible = [o for o in by_delay if o["feasible_under_policy"] and o["flight_no"] != plan["alternative_flight"]]
    if feasible:
        o = feasible[0]
        cabin = Cabin(pax["cabin"])
        if o["seats_left_after_plan"][cabin.value.lower()] <= 0:
            cabin = Cabin.ECONOMY
        return ExceptionProposal(
            passenger_id=pid,
            action=ResolutionAction.REASSIGN_TO_OPTION,
            flight_no=o["flight_no"],
            cabin=cabin,
            policy_ids=plan["policies"],
            rationale=f"{o['flight_no']} is feasible under every retrieved policy and arrives +{o['arrival_delay_min']} min.",
            alternatives_considered=[x["flight_no"] for x in feasible[1:3]],
        )

    waivable = [o for o in by_delay if o["waivable_by_duty_manager"]]
    if waivable:
        o = waivable[0]
        waived = sorted({i for b in o["blocked_by"] for i in _POLICY_ID.findall(b)})
        return ExceptionProposal(
            passenger_id=pid,
            action=ResolutionAction.REQUEST_POLICY_WAIVER,
            flight_no=o["flight_no"],
            policy_ids=waived,
            rationale=f"Only a policy blocks {o['flight_no']} ({'; '.join(o['blocked_by'])}); "
            "a duty-manager waiver would re-protect the passenger, otherwise refund.",
            alternatives_considered=[x["flight_no"] for x in waivable[1:3]],
        )

    return ExceptionProposal(
        passenger_id=pid,
        action=ResolutionAction.OFFER_REFUND,
        policy_ids=plan["policies"],
        rationale="No alternative is feasible or waivable; offer a refund with duty of care.",
    )
