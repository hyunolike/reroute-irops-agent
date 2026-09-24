"""Explicit domain vocabularies."""

from enum import StrEnum


class FlightStatus(StrEnum):
    SCHEDULED = "SCHEDULED"
    DELAYED = "DELAYED"
    CANCELLED = "CANCELLED"


class Cabin(StrEnum):
    ECONOMY = "ECONOMY"
    BUSINESS = "BUSINESS"


class ReservationStatus(StrEnum):
    CONFIRMED = "CONFIRMED"
    DISRUPTED = "DISRUPTED"
    REBOOKED = "REBOOKED"


class DisruptionType(StrEnum):
    CANCELLATION = "CANCELLATION"
    DELAY = "DELAY"


class AgentState(StrEnum):
    RECEIVED = "RECEIVED"
    ANALYZING_DISRUPTION = "ANALYZING_DISRUPTION"
    FETCHING_PASSENGERS = "FETCHING_PASSENGERS"
    SEARCHING_ALTERNATIVES = "SEARCHING_ALTERNATIVES"
    RETRIEVING_POLICIES = "RETRIEVING_POLICIES"
    OPTIMIZING = "OPTIMIZING"
    GENERATING_PROPOSAL = "GENERATING_PROPOSAL"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    EXECUTING = "EXECUTING"
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"


TERMINAL_STATES = {AgentState.COMPLETED, AgentState.REJECTED, AgentState.FAILED}


class PlanStatus(StrEnum):
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    EXECUTED = "EXECUTED"
    PARTIALLY_EXECUTED = "PARTIALLY_EXECUTED"


class ApprovalStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class AssignmentStatus(StrEnum):
    AUTO_ASSIGNED = "AUTO_ASSIGNED"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    NO_FEASIBLE = "NO_FEASIBLE"
    EXECUTED = "EXECUTED"
    EXECUTION_FAILED = "EXECUTION_FAILED"
    HELD_FOR_OPERATOR = "HELD_FOR_OPERATOR"


class EventType(StrEnum):
    STATE_CHANGED = "STATE_CHANGED"
    PLANNER = "PLANNER"
    TOOL_CALL = "TOOL_CALL"
    TOOL_RESULT = "TOOL_RESULT"
    TOOL_ERROR = "TOOL_ERROR"
    GUARDRAIL = "GUARDRAIL"
    APPROVAL = "APPROVAL"
    REPORT = "REPORT"


class Component(StrEnum):
    """Which runtime component produced an event - shown in the UI as a badge."""

    ORCHESTRATOR = "orchestrator"
    NEMOTRON = "nemotron"
    MOCK_LLM = "mock-llm"
    AIRLINE_API = "airline-api"
    NEMO_RETRIEVER = "nemo-retriever"
    LEXICAL_RETRIEVER = "lexical-retriever"
    CUOPT = "cuopt"
    FALLBACK_SOLVER = "fallback-solver"
    APPROVAL_GATEWAY = "approval-gateway"
    OPENSHELL = "openshell"
    POLICY_MIRROR = "policy-mirror"
