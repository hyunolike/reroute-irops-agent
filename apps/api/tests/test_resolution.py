"""Exception-resolution verifier: the deterministic gate between the reasoning model's recommendations and the operator.

KE123 demo plan: P010 NO_FEASIBLE (SQ637 connection; only 7C1102 - no interline agreement - would protect it),
P011 MANUAL_REVIEW on KE701 (tight connection), P013 WCHC and P014 UMNR MANUAL_REVIEW on KE703.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.domain.enums import AssignmentStatus, Cabin
from app.domain.models import OptimizationRequest, OptimizationResult, PolicyRules
from app.rag.compiler import compile_policy_rules
from app.resolution.models import ExceptionProposal, ResolutionAction, Verdict, ViolationCode
from app.resolution.verifier import unresolved_exceptions, verify_proposals
from app.services.airline import AirlineService
from tests.conftest import COMMAND, SERVICE_DATE, run_agent_to_approval
from tests.test_optimization import optimizer

A = ResolutionAction


@dataclass
class Case:
    req: OptimizationRequest
    result: OptimizationResult
    known: set[str]

    def verify(self, *proposals: ExceptionProposal, req: OptimizationRequest | None = None):
        return verify_proposals(list(proposals), req or self.req, self.result.assignments, self.known)

    def one(self, **kw):
        return self.verify(proposal(**kw))[0]

    def seat(self, pid: str):
        a = next(a for a in self.result.assignments if a.passenger_id == pid)
        return a.alternative_flight, a.new_cabin, a.status


def proposal(**kw) -> ExceptionProposal:
    return ExceptionProposal(**{"rationale": "grounded in the exception analysis", **kw})


def codes(res) -> set[ViolationCode]:
    return {v.code for v in res.violations}


@pytest.fixture
async def case(container) -> Case:
    with container.db.session() as s:
        svc = AirlineService(s)
        flight = svc.get_flight("KE123")
        pax = svc.affected_passengers("KE123")
        alts = svc.search_alternatives("ICN", "NRT", SERVICE_DATE, exclude_flight_no="KE123")
    hits = []
    for ch in container.lexical.chunks:
        hits += await container.lexical.search(ch.title, 1)
    req = OptimizationRequest(disrupted_flight=flight, passengers=pax, alternatives=alts, rules=compile_policy_rules(hits))
    return Case(req, await optimizer().optimize(req), {h.policy_id for h in hits})


async def test_demo_exceptions_are_where_the_scenario_says(case):
    assert case.seat("P010") == (None, None, AssignmentStatus.NO_FEASIBLE)
    assert case.seat("P011") == ("KE701", Cabin.ECONOMY, AssignmentStatus.MANUAL_REVIEW)
    assert case.seat("P013")[0::2] == ("KE703", AssignmentStatus.MANUAL_REVIEW)
    assert case.seat("P014")[0::2] == ("KE703", AssignmentStatus.MANUAL_REVIEW)


# ------------------------------------------------------------------ happy paths
async def test_confirm_manual_review_with_checklist_passes(case):
    r = case.one(passenger_id="P013", action=A.CONFIRM_SOLVER_ASSIGNMENT, checklist=["WCHC ground handling at NRT"])
    assert r.verdict == Verdict.PASS and r.seat == ("KE703", Cabin.ECONOMY) and r.required_role is None


async def test_policy_blocked_connection_saver_needs_duty_manager(case):
    r = case.one(passenger_id="p010", action=A.REQUEST_POLICY_WAIVER, flight_no="7c1102", policy_ids=["IROP-002"])
    assert r.verdict == Verdict.PASS_REQUIRES_WAIVER, r.violations
    assert r.required_role == "duty_manager"
    assert r.seat == ("7C1102", Cabin.ECONOMY)
    assert r.waived == ["C6 IROP-002: carrier 7C has no interline agreement"]


async def test_refund_passes_without_a_seat(case):
    r = case.one(passenger_id="P010", action=A.OFFER_REFUND, policy_ids=["IROP-002"])
    assert r.verdict == Verdict.PASS and r.seat is None


# ------------------------------------------------------------------ what the model must not get away with
async def test_invented_flight_rejected(case):
    r = case.one(passenger_id="P010", action=A.REASSIGN_TO_OPTION, flight_no="KE999")
    assert r.verdict == Verdict.REJECTED and codes(r) == {ViolationCode.UNKNOWN_FLIGHT}


async def test_invented_policy_rejected(case):
    r = case.one(passenger_id="P010", action=A.OFFER_REFUND, policy_ids=["IROP-999"])
    assert r.verdict == Verdict.REJECTED and codes(r) == {ViolationCode.CITATION}


async def test_reassign_to_policy_blocked_flight_rejected_with_waiver_hint(case):
    r = case.one(passenger_id="P010", action=A.REASSIGN_TO_OPTION, flight_no="7C1102")
    assert codes(r) == {ViolationCode.CONSTRAINT}
    assert "waiver" in r.violations[0].message


@pytest.mark.parametrize(
    ("pid", "flight", "why"),
    [
        ("P010", "KE2101", "airport change"),  # lands HND, SQ637 leaves NRT: C4 is physical
        ("P010", "OZ102", "MCT"),  # misses the connection
        ("P013", "7C1102", "WCHC must stay on own carrier"),  # SSR safety rule, even though interline is waivable
        ("P011", "KE125", "itself disrupted"),  # operational fact, not a policy choice
    ],
)
async def test_waiver_never_lifts_operational_or_safety_constraints(case, pid, flight, why):
    r = case.one(passenger_id=pid, action=A.REQUEST_POLICY_WAIVER, flight_no=flight, policy_ids=sorted(case.known))
    assert r.verdict == Verdict.REJECTED and ViolationCode.NOT_WAIVABLE in codes(r)
    assert any(why in v.message for v in r.violations)
    assert r.required_role is None and r.seat is None


async def test_waiver_must_cite_the_policy_it_waives(case):
    r = case.one(passenger_id="P010", action=A.REQUEST_POLICY_WAIVER, flight_no="7C1102", policy_ids=["MCT-002"])
    assert codes(r) == {ViolationCode.CITATION}


async def test_waiver_for_a_feasible_flight_is_not_a_waiver(case):
    r = case.one(passenger_id="P013", action=A.REQUEST_POLICY_WAIVER, flight_no="KE701", policy_ids=["IROP-001"])
    assert ViolationCode.NO_WAIVER_NEEDED in codes(r)


async def test_auto_assigned_passengers_are_off_limits(case):
    auto = next(a for a in case.result.assignments if a.status == AssignmentStatus.AUTO_ASSIGNED)
    r = case.one(passenger_id=auto.passenger_id, action=A.OFFER_REFUND)
    assert codes(r) == {ViolationCode.TARGET}


@pytest.mark.parametrize(
    ("kw", "code"),
    [
        (dict(passenger_id="P011", action=A.CONFIRM_SOLVER_ASSIGNMENT), ViolationCode.CHECKLIST),
        (dict(passenger_id="P010", action=A.CONFIRM_SOLVER_ASSIGNMENT, checklist=["x"]), ViolationCode.TARGET),
        (dict(passenger_id="P011", action=A.REASSIGN_TO_OPTION, flight_no="KE701"), ViolationCode.PARAMS),
        (dict(passenger_id="P010", action=A.OFFER_REFUND, flight_no="7C1102"), ViolationCode.PARAMS),
        (dict(passenger_id="P010", action=A.REASSIGN_TO_OPTION), ViolationCode.PARAMS),
        (dict(passenger_id="P013", action=A.REASSIGN_TO_OPTION, flight_no="KE705", cabin=Cabin.BUSINESS), ViolationCode.CABIN),
        (dict(passenger_id="P010", action=A.OFFER_REFUND, rationale=" "), ViolationCode.RATIONALE),
    ],
)
async def test_malformed_proposals_rejected(case, kw, code):
    r = case.verify(ExceptionProposal(**{"rationale": "r", **kw}))[0]
    assert r.verdict == Verdict.REJECTED and code in codes(r), r.violations


async def test_duplicate_proposal_rejected(case):
    first, second = case.verify(
        proposal(passenger_id="P010", action=A.OFFER_REFUND), proposal(passenger_id="P010", action=A.REROUTE_OFFLINE)
    )
    assert first.verdict == Verdict.PASS and codes(second) == {ViolationCode.DUPLICATE}


# ------------------------------------------------------------------ seat capacity across proposals
def with_seats(req: OptimizationRequest, flight_no: str, **seats) -> OptimizationRequest:
    alts = [f.model_copy(update=seats) if f.flight_no == flight_no else f for f in req.alternatives]
    return req.model_copy(update={"alternatives": alts})


async def test_two_proposals_cannot_share_the_last_seat(case):
    req = with_seats(case.req, "7C1102", economy_available=1)
    both = [
        proposal(passenger_id=p, action=A.REQUEST_POLICY_WAIVER, flight_no="7C1102", policy_ids=["IROP-002"])
        for p in ("P010", "P011")
    ]
    assert all(codes(r) == {ViolationCode.CAPACITY} for r in case.verify(*both, req=req))
    # each is fine on its own - only the joint check catches it
    assert case.verify(both[0], req=req)[0].accepted and case.verify(both[1], req=req)[0].accepted


async def test_released_seat_can_be_reused_but_only_if_the_release_holds(case):
    # KE701 economy is full after the plan; P011 moving off it frees the seat P013 (own-carrier WCHC) can take
    move_p011 = proposal(passenger_id="P011", action=A.REQUEST_POLICY_WAIVER, flight_no="7C1102", policy_ids=["IROP-002"])
    p013_to_ke701 = proposal(passenger_id="P013", action=A.REASSIGN_TO_OPTION, flight_no="KE701")
    alone = case.verify(p013_to_ke701)[0]
    assert codes(alone) == {ViolationCode.CAPACITY}

    a, b = case.verify(move_p011, p013_to_ke701)
    assert a.verdict == Verdict.PASS_REQUIRES_WAIVER and b.verdict == Verdict.PASS

    # if P011's move is rejected, the seat it would have freed is gone again
    bad_move = move_p011.model_copy(update={"policy_ids": []})
    a, b = case.verify(bad_move, p013_to_ke701)
    assert codes(a) == {ViolationCode.CITATION} and codes(b) == {ViolationCode.CAPACITY}


async def test_unresolved_exceptions(case):
    verified = case.verify(
        proposal(passenger_id="P010", action=A.OFFER_REFUND),
        proposal(passenger_id="P011", action=A.CONFIRM_SOLVER_ASSIGNMENT),  # rejected: no checklist
    )
    assert unresolved_exceptions(case.result.assignments, verified) == ["P011", "P013", "P014"]


# ------------------------------------------------------------------ one source of truth
async def test_verifier_agrees_with_the_options_shown_to_the_model(client, container):
    """For every exception passenger and every alternative the model sees, the verifier accepts a single
    REASSIGN exactly when explore_exception_options says the option is feasible, and a WAIVER exactly when it
    says the option is waivable. The model is never shown an option the verifier would then reject."""
    task = await run_agent_to_approval(client, container, COMMAND)
    evs = (await client.get(f"/api/agent/tasks/{task['id']}/events", params={"stream": False})).json()["events"]
    opt = next(e["detail"] for e in evs if e["type"] == "TOOL_RESULT" and e["detail"].get("tool") == "optimize_rebooking")
    explored = {
        e["detail"]["passenger"]["passenger_id"]: e["detail"]
        for e in evs
        if e["type"] == "TOOL_RESULT" and e["detail"].get("tool") == "explore_exception_options"
    }
    with container.db.session() as s:
        svc = AirlineService(s)
        flight = svc.get_flight("KE123")
        req = OptimizationRequest(
            disrupted_flight=flight,
            passengers=svc.affected_passengers("KE123"),
            alternatives=svc.search_alternatives("ICN", "NRT", SERVICE_DATE, exclude_flight_no="KE123"),
            rules=PolicyRules.model_validate(opt["rules"]),
        )
    result = OptimizationResult.model_validate(opt["result"])
    known = set(req.rules.applied.values())
    assert set(explored) == {"P010", "P011", "P013", "P014"}

    checked = 0
    for pid, view in explored.items():
        current = view["current_plan"]["alternative_flight"]
        for o in view["options"]:
            if o["flight_no"] == current:
                continue
            reassign = verify_proposals(
                [proposal(passenger_id=pid, action=A.REASSIGN_TO_OPTION, flight_no=o["flight_no"])],
                req,
                result.assignments,
                known,
            )[0]
            waiver = verify_proposals(
                [proposal(passenger_id=pid, action=A.REQUEST_POLICY_WAIVER, flight_no=o["flight_no"], policy_ids=sorted(known))],
                req,
                result.assignments,
                known,
            )[0]
            assert reassign.accepted == o["feasible_under_policy"], (pid, o, reassign.violations)
            assert waiver.accepted == o["waivable_by_duty_manager"], (pid, o, waiver.violations)
            checked += 1
    assert checked >= 20
