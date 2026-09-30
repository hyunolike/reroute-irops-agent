"""The exception-resolution eval must pass a sound planner and fail an unsound one - otherwise its gates mean nothing."""

from __future__ import annotations

import copy

import pytest
import yaml

from app.evals.exceptions import GATES, GOLDEN, evaluate
from app.providers.llm.base import LLMResponse
from app.providers.llm.mock import MockLLMProvider
from tests.conftest import ROOT

BASELINE = {"ke123-baseline"}


@pytest.fixture(scope="module")
def golden():
    return yaml.safe_load(GOLDEN.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def gates():
    return yaml.safe_load(GATES.read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _project_root(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("PROJECT_ROOT", str(ROOT))


class Scripted(MockLLMProvider):
    """The scripted planner with its own exception recommendations: `choose(view) -> proposal kwargs`."""

    def __init__(self, choose):
        self.choose = choose

    def _resolve_exceptions(self, called, call):
        if "propose_exception_resolution" in called:
            return super()._resolve_exceptions(called, call)  # corrections: the stock refund fallback
        views = [self._last_json([e]) for e in called.get("explore_exception_options", [])]
        return LLMResponse(
            content="recommending",
            tool_calls=[call("propose_exception_resolution", **self.choose(v)) for v in views if "passenger" in v],
            model=self.model,
        )


def refund_everyone(view):
    return {"passenger_id": view["passenger"]["passenger_id"], "action": "OFFER_REFUND", "rationale": "refund"}


def invent_a_flight(view):
    return {
        "passenger_id": view["passenger"]["passenger_id"],
        "action": "REASSIGN_TO_OPTION",
        "flight_no": "KE999",
        "rationale": "r",
    }


async def test_the_scripted_planner_passes_every_gate_on_the_whole_golden_set(golden, gates):
    report = await evaluate(golden, gates)
    assert report["passed"], report["gate_failures"]
    assert report["metrics"]["golden_set_drift"] == 0 and report["metrics"]["action_accuracy"] == 1.0
    assert report["cases"] == len(golden["cases"]) >= 5


async def test_a_planner_that_refunds_everyone_fails_accuracy(golden, gates):
    report = await evaluate(golden, gates, only=BASELINE, planner=lambda: Scripted(refund_everyone))
    m = report["metrics"]
    assert m["first_pass_accept_rate"] == 1.0  # every refund verifies - validity alone is not quality
    assert m["action_accuracy"] == 0.25 < m["baseline_action_accuracy"] == 1.0  # only P010 may be refunded
    assert any(f.startswith("action_accuracy") for f in report["gate_failures"])


async def test_a_planner_that_invents_flights_fails_first_pass(golden, gates):
    report = await evaluate(golden, gates, only=BASELINE, planner=lambda: Scripted(invent_a_flight))
    m = report["metrics"]
    assert m["first_pass_accept_rate"] == 0.0 and m["verifier_bypass"] == 0  # rejected, never shown
    assert any(f.startswith("first_pass_accept_rate") for f in report["gate_failures"])


async def test_an_inconsistent_planner_fails_consistency(golden, gates):
    runs = iter(range(100))

    def flip_flop(view):
        pid = view["passenger"]["passenger_id"]
        if pid == "P011" and next(runs) % 2:  # both acceptable - but not the same answer twice
            return {
                "passenger_id": pid,
                "action": "REQUEST_POLICY_WAIVER",
                "flight_no": "7C1102",
                "policy_ids": ["IROP-002"],
                "rationale": "r",
            }
        return MockLLMProvider.baseline(view)

    report = await evaluate(golden, gates, only=BASELINE, repeats=2, planner=lambda: Scripted(flip_flop))
    m = report["metrics"]
    assert m["action_accuracy"] == 1.0 and m["consistency"] == 0.75
    assert any(f.startswith("consistency") for f in report["gate_failures"])


async def test_a_stale_label_is_reported_as_drift_not_scored(golden, gates):
    stale = copy.deepcopy(golden)
    case = next(c for c in stale["cases"] if c["id"] == "ke123-baseline")
    case["exceptions"]["P010"]["flights"] = ["KE701"]  # KE701 misses SQ637's MCT: not waivable
    del case["exceptions"]["P014"]  # and the solver still leaves P014 as an exception
    report = await evaluate(stale, gates, only=BASELINE)
    assert report["metrics"]["golden_set_drift"] == 2 and report["rows"] == []  # one per problem; the run is not scored
    drift = " | ".join(report["drift"])
    assert "P010: labelled flight KE701" in drift and "!= labelled" in drift
    assert any(f.startswith("golden_set_drift") for f in report["gate_failures"])
