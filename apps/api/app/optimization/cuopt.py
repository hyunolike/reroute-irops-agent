"""NVIDIA cuOpt adapter (self-hosted cuOpt server REST API).

Protocol (cuOpt server, see nvidia/cuopt/README.md and the NVIDIA `cuopt-server-api-python` skill):
    POST {base}/cuopt/request            body = LP/MILP JSON, header CLIENT-VERSION: custom
      -> {"reqId": "..."}  (or the full response if solved within the wait window)
    GET  {base}/cuopt/solution/{reqId}   poll until the body contains "response"
      -> {"response": {"solver_response": {"status": "Optimal",
                                            "solution": {"primal_solution": [...],
                                                         "primal_objective": ..., "solver_time": ...}}}}
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any

import httpx
import numpy as np

from app.optimization.base import OptimizationProvider, SolverError, SolverOutput
from app.optimization.config import SolverConfig
from app.optimization.formulation import MilpProblem

_REQ_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
_HEADERS = {"Content-Type": "application/json", "CLIENT-VERSION": "custom"}
_OK = {"Optimal", "FeasibleFound", "TimeLimit"}


def to_cuopt_payload(problem: MilpProblem, config: SolverConfig) -> dict[str, Any]:
    """Serialise a MilpProblem into the cuOpt server LP/MILP data model (CSR form)."""
    solver_config: dict[str, Any] = {"time_limit": config.time_limit_seconds}
    if config.mip_relative_gap > 0:
        solver_config["tolerances"] = {"mip_relative_gap": config.mip_relative_gap}
    return {
        "csr_constraint_matrix": {
            "offsets": list(problem.offsets),
            "indices": list(problem.indices),
            "values": [float(v) for v in problem.values],
        },
        "constraint_bounds": {
            "lower_bounds": [float(v) for v in problem.row_lb],
            "upper_bounds": [float(v) for v in problem.row_ub],
        },
        "objective_data": {"coefficients": [float(v) for v in problem.objective], "offset": 0.0},
        "variable_bounds": {
            "lower_bounds": [0.0] * problem.n_vars,
            "upper_bounds": [1.0] * problem.n_vars,
        },
        "variable_types": ["I"] * problem.n_vars,
        "variable_names": list(problem.var_names),
        "maximize": False,
        "solver_config": solver_config,
    }


def parse_cuopt_response(body: dict[str, Any], n_vars: int) -> tuple[str, np.ndarray, float, float | None]:
    resp = body.get("response", body)
    solver_resp = resp.get("solver_response", resp)
    status = str(solver_resp.get("status", "Unknown"))
    sol = solver_resp.get("solution") or {}
    primal = sol.get("primal_solution")
    if primal is None and isinstance(sol.get("vars"), dict):
        raise SolverError("cuOpt returned named vars without primal_solution; cannot map to model order")
    if status not in _OK or primal is None:
        raise SolverError(f"cuOpt status={status}: {solver_resp.get('error') or body}")
    values = np.round(np.asarray(primal, dtype=float))
    if values.shape[0] != n_vars:
        raise SolverError(f"cuOpt returned {values.shape[0]} values, expected {n_vars}")
    return status, values, float(sol.get("primal_objective") or 0.0), sol.get("solver_time")


class CuOptOptimizationProvider(OptimizationProvider):
    name = "cuopt"
    nvidia = True

    def __init__(self, base_url: str, poll_timeout_s: float = 60.0, http: httpx.AsyncClient | None = None):
        self.base_url = base_url.rstrip("/")
        self.poll_timeout_s = poll_timeout_s
        self._http = http

    def _client(self) -> httpx.AsyncClient:
        return self._http or httpx.AsyncClient(timeout=30.0)

    async def health(self) -> dict:
        try:
            async with self._client() as c:
                r = await c.get(f"{self.base_url}/cuopt/health", timeout=3)
            return {"provider": self.name, "ok": r.status_code == 200, "url": self.base_url}
        except httpx.HTTPError as e:
            return {"provider": self.name, "ok": False, "url": self.base_url, "error": str(e)}

    async def solve(self, problem: MilpProblem, config: SolverConfig) -> SolverOutput:
        payload = to_cuopt_payload(problem, config)
        start = time.perf_counter()
        client = self._client()
        try:
            r = await client.post(f"{self.base_url}/cuopt/request", json=payload, headers=_HEADERS)
            r.raise_for_status()
            body = r.json()
            deadline = time.monotonic() + self.poll_timeout_s
            while "response" not in body:
                req_id = str(body.get("reqId", ""))
                if not _REQ_ID.fullmatch(req_id):
                    raise SolverError(f"unexpected cuOpt reply: {body}")
                if time.monotonic() > deadline:
                    raise SolverError(f"cuOpt request {req_id} timed out")
                await asyncio.sleep(0.25)
                r = await client.get(f"{self.base_url}/cuopt/solution/{req_id}", headers=_HEADERS)
                r.raise_for_status()
                body = r.json()
        except httpx.HTTPError as e:
            raise SolverError(f"cuOpt server unreachable at {self.base_url}: {e}") from e
        finally:
            if self._http is None:
                await client.aclose()
        status, values, objective, solver_time = parse_cuopt_response(body, problem.n_vars)
        elapsed = (time.perf_counter() - start) * 1000
        return SolverOutput(
            status="OPTIMAL" if status == "Optimal" else "FEASIBLE",
            values=values,
            objective=objective,
            solve_time_ms=(solver_time * 1000) if solver_time else elapsed,
            solver=f"NVIDIA cuOpt MILP ({status})",
            raw={"cuopt_status": status},
        )
