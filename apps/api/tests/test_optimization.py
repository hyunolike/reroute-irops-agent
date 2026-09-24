"""Optimization constraints (C1-C6), objective weights, and the cuOpt adapter contract."""

from collections import Counter

import httpx
import pytest

from app.domain.enums import AssignmentStatus, Cabin
from app.domain.models import OptimizationRequest
from app.optimization.config import OptimizationConfig, Weights
from app.optimization.cuopt import CuOptOptimizationProvider, parse_cuopt_response, to_cuopt_payload
from app.optimization.fallback import FallbackOptimizationProvider
from app.optimization.formulation import build_formulation
from app.optimization.service import RebookingOptimizer
from app.rag.compiler import compile_policy_rules
from app.services.airline import AirlineService
from app.services.optimization import OptimizationService
from tests.conftest import ROOT, SERVICE_DATE


@pytest.fixture
async def request_(container) -> OptimizationRequest:
    with container.db.session() as s:
        svc = AirlineService(s)
        flight = svc.get_flight("KE123")
        pax = svc.affected_passengers("KE123")
        alts = svc.search_alternatives("ICN", "NRT", SERVICE_DATE, exclude_flight_no="KE123")
    hits = []
    for ch in container.lexical.chunks:
        hits += await container.lexical.search(ch.title, 1)
    return OptimizationRequest(disrupted_flight=flight, passengers=pax, alternatives=alts, rules=compile_policy_rules(hits))


def optimizer(**weights) -> RebookingOptimizer:
    cfg = OptimizationConfig.load(ROOT / "config" / "optimization.yaml")
    cfg.weights = Weights.model_validate({**cfg.weights.model_dump(), **weights})
    return RebookingOptimizer(FallbackOptimizationProvider(), cfg)


async def test_demo_scenario_counts(request_):
    res = await optimizer().optimize(request_)
    s = res.summary
    assert res.status == "OPTIMAL"
    assert (s.affected, s.auto_assigned, s.manual_review, s.no_feasible) == (35, 31, 3, 1)
    assert s.business_downgrades == 1


async def test_c1_each_passenger_at_most_one_flight(request_):
    res = await optimizer().optimize(request_)
    assert len({a.passenger_id for a in res.assignments}) == len(res.assignments) == 35


async def test_c2_capacity_never_exceeded(request_):
    res = await optimizer().optimize(request_)
    load = Counter((a.alternative_flight, a.new_cabin) for a in res.assignments if a.alternative_flight)
    by_no = {f.flight_no: f for f in request_.alternatives}
    for (fno, cabin), n in load.items():
        assert n <= by_no[fno].available(cabin), (fno, cabin, n)


async def test_c3_business_kept_when_possible_and_vip_not_downgraded(request_):
    res = await optimizer().optimize(request_)
    business_seats = sum(f.business_available for f in request_.alternatives if f.flight_no in {"KE701", "OZ102", "KE703"})
    biz = [a for a in res.assignments if a.original_cabin == Cabin.BUSINESS]
    kept = sum(a.new_cabin == Cabin.BUSINESS for a in biz)
    assert kept == min(len(biz), business_seats)
    assert all(a.new_cabin == Cabin.BUSINESS for a in biz if a.vip)


async def test_c4_minimum_connection_time_respected(request_):
    res = await optimizer().optimize(request_)
    pax = {p.passenger_id: p for p in request_.passengers}
    for a in res.assignments:
        p = pax[a.passenger_id]
        if p.is_connection and a.new_arrival_time:
            assert (p.onward_departure_time - a.new_arrival_time).total_seconds() / 60 >= request_.rules.mct_minutes
    p010 = next(a for a in res.assignments if a.passenger_id == "P010")
    assert p010.status == AssignmentStatus.NO_FEASIBLE and "7C1102" in p010.reason


async def test_c5_c6_excluded_flights_never_used(request_):
    res = await optimizer().optimize(request_)
    used = {a.alternative_flight for a in res.assignments}
    excluded = {e.flight_no: e for e in res.excluded_flights}
    assert excluded["KE2101"].constraint == "C5"  # HND - co-terminal (IROP-005)
    assert excluded["7C1102"].policy_id == "IROP-002"  # no interline agreement
    assert excluded["KE705"].policy_id == "IROP-003"  # > 12h window
    assert not used & set(excluded)


async def test_ssr_passengers_stay_on_own_carrier_and_need_review(request_):
    res = await optimizer().optimize(request_)
    for a in res.assignments:
        if a.special_assistance in {"WCHC", "UMNR"}:
            assert a.alternative_flight.startswith("KE")
            assert a.status == AssignmentStatus.MANUAL_REVIEW


async def test_weights_are_configurable(request_):
    # Making interline prohibitively expensive empties OZ102; 2 more passengers can no longer be seated.
    res = await optimizer(rebooking_cost={"own_carrier": 20.0, "interline": 1e9}).optimize(request_)
    assert not any(a.alternative_flight == "OZ102" for a in res.assignments)


async def test_optimizer_beats_fcfs_baseline(request_):
    res = await optimizer().optimize(request_)
    b = res.baseline
    assert b.missed_connections > 0 and b.ssr_violations > 0
    assert res.summary.vip_avg_delay_minutes <= b.vip_avg_delay_minutes


def test_cuopt_payload_matches_server_schema(request_):
    form = build_formulation(request_, OptimizationConfig().weights)
    p = form.problem
    payload = to_cuopt_payload(p, OptimizationConfig().solver)
    assert set(payload) >= {
        "csr_constraint_matrix",
        "constraint_bounds",
        "objective_data",
        "variable_bounds",
        "variable_types",
        "maximize",
        "solver_config",
    }
    csr = payload["csr_constraint_matrix"]
    assert len(csr["offsets"]) == p.n_rows + 1 and csr["offsets"][-1] == len(csr["indices"]) == len(csr["values"])
    assert payload["variable_types"] == ["I"] * p.n_vars
    assert payload["maximize"] is False
    assert len(payload["objective_data"]["coefficients"]) == p.n_vars


async def test_cuopt_provider_polls_and_decodes(request_):
    form = build_formulation(request_, OptimizationConfig().weights)
    reference = await FallbackOptimizationProvider().solve(form.problem, OptimizationConfig().solver)
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append((req.method, req.url.path, req.headers.get("CLIENT-VERSION")))
        if req.url.path == "/cuopt/request":
            return httpx.Response(200, json={"reqId": "abc-123"})
        return httpx.Response(
            200,
            json={
                "response": {
                    "solver_response": {
                        "status": "Optimal",
                        "solution": {
                            "primal_solution": reference.values.tolist(),
                            "primal_objective": reference.objective,
                            "solver_time": 0.012,
                        },
                    }
                }
            },
        )

    provider = CuOptOptimizationProvider("http://cuopt:5000", http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    out = await provider.solve(form.problem, OptimizationConfig().solver)
    assert calls[0] == ("POST", "/cuopt/request", "custom") and calls[1][:2] == ("GET", "/cuopt/solution/abc-123")
    assert out.solver.startswith("NVIDIA cuOpt") and out.objective == pytest.approx(reference.objective)
    assert (out.values == reference.values).all()


def test_parse_cuopt_error_status():
    with pytest.raises(Exception, match="Infeasible"):
        parse_cuopt_response({"response": {"solver_response": {"status": "Infeasible", "solution": {}}}}, 3)


async def test_cuopt_unreachable_falls_back_with_label(request_):
    cfg = OptimizationConfig()
    unreachable = CuOptOptimizationProvider("http://127.0.0.1:9", poll_timeout_s=1)
    service = OptimizationService(RebookingOptimizer(unreachable, cfg), RebookingOptimizer(FallbackOptimizationProvider(), cfg))
    res = await service.optimize(request_)
    assert res.provider == "fallback" and res.nvidia is False
    assert "cuOpt unavailable" in res.notes[0]
