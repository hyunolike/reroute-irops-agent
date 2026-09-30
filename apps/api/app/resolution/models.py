"""Contracts for exception resolution: what the reasoning model may propose for a passenger the solver could not
auto-assign, and what the deterministic verifier says about it."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from app.domain.enums import Cabin


class ResolutionAction(StrEnum):
    CONFIRM_SOLVER_ASSIGNMENT = "CONFIRM_SOLVER_ASSIGNMENT"  # MANUAL_REVIEW: keep the solver's flight, with a checklist
    REASSIGN_TO_OPTION = "REASSIGN_TO_OPTION"  # move to another alternative that is feasible under policy
    REQUEST_POLICY_WAIVER = "REQUEST_POLICY_WAIVER"  # flight blocked only by a waivable policy -> duty manager signs
    OFFER_REFUND = "OFFER_REFUND"  # no booking change; refund / duty of care follow-up
    REROUTE_OFFLINE = "REROUTE_OFFLINE"  # no booking change here; handled outside the system (other carrier, ground)


FLIGHT_ACTIONS = frozenset({ResolutionAction.REASSIGN_TO_OPTION, ResolutionAction.REQUEST_POLICY_WAIVER})


class ExceptionProposal(BaseModel):
    passenger_id: str
    action: ResolutionAction
    flight_no: str | None = Field(default=None, description="REASSIGN_TO_OPTION / REQUEST_POLICY_WAIVER only")
    cabin: Cabin | None = Field(default=None, description="defaults to the passenger's booked cabin")
    policy_ids: list[str] = Field(default_factory=list, description="retrieved policies this recommendation rests on")
    checklist: list[str] = Field(default_factory=list, description="what the operator must confirm before approving")
    rationale: str = ""
    alternatives_considered: list[str] = Field(default_factory=list)


class Verdict(StrEnum):
    PASS = "PASS"
    PASS_REQUIRES_WAIVER = "PASS_REQUIRES_WAIVER"
    REJECTED = "REJECTED"


class ViolationCode(StrEnum):
    TARGET = "TARGET"  # not an exception passenger / nothing to confirm
    DUPLICATE = "DUPLICATE"  # more than one proposal for the same passenger
    PARAMS = "PARAMS"  # arguments do not fit the action
    RATIONALE = "RATIONALE"  # no explanation for the operator
    CHECKLIST = "CHECKLIST"  # confirmation without what to confirm
    UNKNOWN_FLIGHT = "UNKNOWN_FLIGHT"  # not one of the searched alternatives (invented flight)
    CABIN = "CABIN"  # cabin the passenger is not eligible for
    CONSTRAINT = "CONSTRAINT"  # a hard constraint blocks the flight
    NOT_WAIVABLE = "NOT_WAIVABLE"  # waiver requested for an operational / safety constraint
    NO_WAIVER_NEEDED = "NO_WAIVER_NEEDED"  # waiver requested for a flight nothing blocks
    CAPACITY = "CAPACITY"  # seats exceeded once all proposals are applied together
    CITATION = "CITATION"  # cites an unknown policy, or a waiver omits the policy it waives


class Violation(BaseModel):
    code: ViolationCode
    message: str


class VerifiedProposal(BaseModel):
    proposal: ExceptionProposal
    verdict: Verdict
    violations: list[Violation] = Field(default_factory=list)
    required_role: str | None = None  # "duty_manager" when a policy is waived
    waived: list[str] = Field(default_factory=list)  # the policy blocks a duty manager would be signing off
    seat: tuple[str, Cabin] | None = None  # (flight, cabin) the passenger ends up holding, if any

    @property
    def accepted(self) -> bool:
        return self.verdict != Verdict.REJECTED
