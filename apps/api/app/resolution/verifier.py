"""Deterministic verifier for exception-resolution proposals. No LLM call, no I/O.

The reasoning model may recommend what to do with a passenger the solver could not auto-assign, but only a
proposal that passes this verifier is ever shown to the operator as a recommendation. Feasibility is judged
with the same constraint code the MILP formulation uses (optimization/constraints.py), and seat capacity is
checked for all proposals *together*: each exception is analysed on its own, so two proposals can each look
fine while both claiming the last seat.

Run it again whenever the set of proposals changes (operator edits, partial approval) - never trust an
earlier verdict for a different set.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable

from app.domain.enums import AssignmentStatus, Cabin
from app.domain.models import AffectedPassengerDTO, Assignment, FlightDTO, OptimizationRequest
from app.optimization.constraints import eligible_cabins, flight_blocks, passenger_blocks
from app.resolution.models import (
    FLIGHT_ACTIONS,
    ExceptionProposal,
    ResolutionAction,
    Verdict,
    VerifiedProposal,
    Violation,
    ViolationCode,
)

EXCEPTION_STATUSES = frozenset({AssignmentStatus.MANUAL_REVIEW, AssignmentStatus.NO_FEASIBLE})
WAIVER_ROLE = "duty_manager"

Seat = tuple[str, Cabin]


def verify_proposals(
    proposals: list[ExceptionProposal],
    req: OptimizationRequest,
    assignments: list[Assignment],
    known_policy_ids: Iterable[str],
) -> list[VerifiedProposal]:
    """Verify every proposal on its own, then the seat capacity of all accepted proposals applied together."""
    known = set(known_policy_ids)
    plan = {a.passenger_id: a for a in assignments}
    pax = {p.passenger_id: p for p in req.passengers}
    flights = {f.flight_no: f for f in req.alternatives}

    results: list[VerifiedProposal] = []
    seen: set[str] = set()
    for prop in proposals:
        pid = prop.passenger_id.strip().upper()
        prop = prop.model_copy(
            update={"passenger_id": pid, "flight_no": prop.flight_no.strip().upper() if prop.flight_no else None}
        )
        res = _verify_one(prop, plan.get(pid), pax.get(pid), flights, req, known)
        if pid in seen:
            res.violations.insert(0, Violation(code=ViolationCode.DUPLICATE, message=f"more than one proposal for {pid}"))
        seen.add(pid)
        results.append(res)

    _check_capacity(results, plan, flights)
    for res in results:
        if res.violations:
            res.verdict, res.required_role, res.waived, res.seat = Verdict.REJECTED, None, [], None
    return results


def unresolved_exceptions(assignments: list[Assignment], verified: list[VerifiedProposal]) -> list[str]:
    """Exception passengers that still have no accepted proposal."""
    covered = {v.proposal.passenger_id for v in verified if v.accepted}
    return [a.passenger_id for a in assignments if a.status in EXCEPTION_STATUSES and a.passenger_id not in covered]


# ---------------------------------------------------------------------------------------------- single proposal
def _verify_one(
    prop: ExceptionProposal,
    current: Assignment | None,
    p: AffectedPassengerDTO | None,
    flights: dict[str, FlightDTO],
    req: OptimizationRequest,
    known: set[str],
) -> VerifiedProposal:
    res = VerifiedProposal(proposal=prop, verdict=Verdict.PASS)
    v = res.violations

    def fail(code: ViolationCode, message: str) -> VerifiedProposal:
        v.append(Violation(code=code, message=message))
        return res

    if current is None or p is None:
        return fail(ViolationCode.TARGET, f"unknown passenger {prop.passenger_id}")
    if current.status not in EXCEPTION_STATUSES:
        return fail(ViolationCode.TARGET, f"{p.passenger_id} is {current.status.value}; only MANUAL_REVIEW / NO_FEASIBLE")
    if not prop.rationale.strip():
        fail(ViolationCode.RATIONALE, "rationale is required - the operator decides from it")
    for pid in prop.policy_ids:
        if pid not in known:
            fail(ViolationCode.CITATION, f"{pid} was not retrieved for this task")
    if prop.action not in FLIGHT_ACTIONS and prop.action != ResolutionAction.CONFIRM_SOLVER_ASSIGNMENT:
        if prop.flight_no or prop.cabin:
            fail(ViolationCode.PARAMS, f"{prop.action.value} takes no flight or cabin")
        return res  # refund / offline re-routing: no seat, nothing else to check

    if prop.action == ResolutionAction.CONFIRM_SOLVER_ASSIGNMENT:
        if current.alternative_flight is None or current.new_cabin is None:
            return fail(ViolationCode.TARGET, f"{p.passenger_id} has no solver seat to confirm ({current.status.value})")
        if prop.flight_no not in (None, current.alternative_flight) or prop.cabin not in (None, current.new_cabin):
            fail(ViolationCode.PARAMS, f"confirmation must keep {current.alternative_flight}; use REASSIGN_TO_OPTION to move")
        if not any(item.strip() for item in prop.checklist):
            fail(ViolationCode.CHECKLIST, "say what the operator must confirm (e.g. SSR handling, connection risk)")
        flight_no, cabin = current.alternative_flight, current.new_cabin
    else:
        if not prop.flight_no:
            return fail(ViolationCode.PARAMS, f"{prop.action.value} needs flight_no")
        if prop.flight_no not in flights:
            return fail(ViolationCode.UNKNOWN_FLIGHT, f"{prop.flight_no} is not one of the searched alternatives")
        if prop.flight_no == current.alternative_flight:
            fail(ViolationCode.PARAMS, f"{prop.flight_no} is already the solver's flight; use CONFIRM_SOLVER_ASSIGNMENT")
        flight_no, cabin = prop.flight_no, prop.cabin or p.cabin

    if cabin not in eligible_cabins(p, req.rules):
        fail(ViolationCode.CABIN, f"{p.cabin.value} passenger cannot be seated in {cabin.value}")
    f = flights[flight_no]
    blocks = flight_blocks(f, req) + passenger_blocks(p, f, req)[0]

    if prop.action == ResolutionAction.REQUEST_POLICY_WAIVER:
        if not blocks:
            fail(ViolationCode.NO_WAIVER_NEEDED, f"{flight_no} is feasible under policy; use REASSIGN_TO_OPTION")
        for b in blocks:
            if not b.waivable:
                fail(ViolationCode.NOT_WAIVABLE, f"{flight_no}: {b.describe()} cannot be waived")
            elif b.policy_id and b.policy_id not in prop.policy_ids:
                fail(ViolationCode.CITATION, f"a waiver must cite the policy it waives ({b.policy_id})")
        res.verdict = Verdict.PASS_REQUIRES_WAIVER
        res.required_role = WAIVER_ROLE
        res.waived = [b.describe() for b in blocks if b.waivable]
    else:
        for b in blocks:
            hint = " (only a duty-manager waiver could lift it)" if b.waivable else ""
            fail(ViolationCode.CONSTRAINT, f"{flight_no}: {b.describe()}{hint}")
    res.seat = (flight_no, cabin)
    return res


# ---------------------------------------------------------------------------------------------- seat capacity
def _check_capacity(results: list[VerifiedProposal], plan: dict[str, Assignment], flights: dict[str, FlightDTO]) -> None:
    """Apply all still-valid proposals on top of the solver plan and reject those that overbook a cabin.

    Rejecting a proposal also cancels the seat it would have released, which can overbook another cabin,
    so repeat until stable (the set of valid proposals only shrinks, so this terminates).
    """
    while True:
        active = [r for r in results if not r.violations]
        used: Counter[Seat] = Counter(
            (a.alternative_flight, a.new_cabin) for a in plan.values() if a.alternative_flight and a.new_cabin
        )
        for r in active:
            old = plan[r.proposal.passenger_id]
            old_seat = (old.alternative_flight, old.new_cabin) if old.alternative_flight and old.new_cabin else None
            if r.seat != old_seat:
                if old_seat:
                    used[old_seat] -= 1
                if r.seat:
                    used[r.seat] += 1
        over = {seat for seat, n in used.items() if seat[0] in flights and n > flights[seat[0]].available(seat[1])}
        if not over:
            return
        flagged = False
        for r in active:
            old = plan[r.proposal.passenger_id]
            if r.seat in over and r.seat != (old.alternative_flight, old.new_cabin):
                fno, cabin = r.seat
                r.violations.append(
                    Violation(
                        code=ViolationCode.CAPACITY,
                        message=f"{fno} {cabin.value} has {flights[fno].available(cabin)} seat(s); "
                        f"the plan plus these proposals need {used[r.seat]}",
                    )
                )
                flagged = True
        if not flagged:  # overbooked by the solver plan itself - not something a proposal can fix
            return
