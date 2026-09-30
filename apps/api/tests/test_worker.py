"""Remote agent worker (the process meant to run inside an OpenShell sandbox), over real HTTP."""

import socket
import threading
import time
from contextlib import contextmanager

import httpx
import pytest
import uvicorn

from app.agent.worker import AgentWorker
from tests.conftest import COMMAND, OPERATOR, build_app, make_settings


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextmanager
def serve_control_plane(tmp_path, **overrides):
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    settings = make_settings(
        tmp_path,
        agent_execution="remote",
        agent_worker_token="worker-secret",
        airline_api_base_url=base,
        reroute_api_base_url=base,
        **overrides,
    )
    app, container = build_app(settings)
    container.http.transport = None  # real network from here on
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    try:
        yield base, tmp_path
    finally:
        server.should_exit = True
        t.join(timeout=5)


@pytest.fixture
def live_control_plane(tmp_path):
    with serve_control_plane(tmp_path) as live:
        yield live


def make_worker(base, tmp_path, **overrides) -> AgentWorker:
    return AgentWorker(
        make_settings(
            tmp_path,
            database_url="sqlite:///nonexistent-dir/never-used.db",  # the worker must not need a database
            approval_signing_secret="worker-does-not-know-the-secret",
            agent_worker_token="worker-secret",
            airline_api_base_url=base,
            reroute_api_base_url=base,
            **overrides,
        )
    )


async def test_worker_runs_task_end_to_end_without_db(live_control_plane):
    base, tmp_path = live_control_plane
    worker = make_worker(base, tmp_path)
    async with httpx.AsyncClient(base_url=base) as client:
        task = (await client.post("/api/agent/tasks", json={"command": COMMAND})).json()
        assert task["state"] == "RECEIVED"  # queued for the worker, not run in-process
        assert await worker.run_once() is True
        task = (await client.get(f"/api/agent/tasks/{task['id']}")).json()
        assert task["state"] == "WAITING_APPROVAL"
        # exception recommendations made inside the sandbox reach the control plane through the plan API
        res = (await client.get(f"/api/rebooking/plans/{task['plan_id']}/exception-resolutions")).json()
        assert res["metrics"]["coverage"] == 1.0 and res["metrics"]["waivers_requested"] == ["P010"]

        # Without approval the worker cannot obtain an execution grant
        r = worker.cp.request("POST", f"/internal/agent/plans/{task['plan_id']}/execution-grant")
        assert r.status_code == 403

        await client.post(f"/api/rebooking/plans/{task['plan_id']}/approve", json={}, headers=OPERATOR)
        assert await worker.run_once() is True
        done = (await client.get(f"/api/agent/tasks/{task['id']}")).json()
        assert done["state"] == "COMPLETED" and done["report"]["rebooked"] == 31
        assert await worker.run_once() is False  # queue drained


async def test_internal_api_requires_worker_token(live_control_plane):
    base, _ = live_control_plane
    async with httpx.AsyncClient(base_url=base) as client:
        assert (await client.post("/internal/agent/tasks/claim")).status_code == 401
        r = await client.post("/internal/agent/tasks/claim", headers={"X-Agent-Worker-Token": "wrong"})
        assert r.status_code == 401


def test_worker_cannot_reach_operator_endpoints(live_control_plane, tmp_path):
    base, _ = live_control_plane
    worker = AgentWorker(
        make_settings(tmp_path, agent_worker_token="worker-secret", reroute_api_base_url=base, airline_api_base_url=base)
    )
    from app.security.governed_http import PolicyViolation

    with pytest.raises(PolicyViolation):
        worker.cp.request("POST", "/api/rebooking/plans/any/approve")


def test_worker_waits_for_control_plane_at_startup(live_control_plane, monkeypatch):
    """After a host reboot the sandboxed worker can start before the control plane; it must wait, not exit."""
    base, tmp_path = live_control_plane
    from app.agent import remote, worker

    real = remote.ControlPlaneClient.request
    attempts = {"n": 0}

    def flaky(self, method, path, **kw):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise httpx.ConnectError("control plane still starting")
        if attempts["n"] == 2:
            return httpx.Response(503, request=httpx.Request(method, base + path))
        return real(self, method, path, **kw)

    sleeps = []
    monkeypatch.setattr(remote.ControlPlaneClient, "request", flaky)
    monkeypatch.setattr(worker.time, "sleep", sleeps.append)
    settings = make_settings(tmp_path, agent_worker_token="worker-secret", reroute_api_base_url=base, airline_api_base_url=base)
    AgentWorker(settings)
    assert sleeps == [1.0, 2.0]


def test_worker_fails_fast_on_wrong_token(live_control_plane, monkeypatch):
    base, tmp_path = live_control_plane
    from app.agent import worker

    monkeypatch.setattr(worker.time, "sleep", lambda s: pytest.fail("a 4xx must not be retried"))
    settings = make_settings(tmp_path, agent_worker_token="wrong-token", reroute_api_base_url=base, airline_api_base_url=base)
    with pytest.raises(httpx.HTTPStatusError):
        AgentWorker(settings)


async def test_dashboard_reports_where_the_agent_actually_runs(live_control_plane):
    """The control plane itself uses the policy mirror; a worker in an OpenShell sandbox says so on every call."""
    base, tmp_path = live_control_plane
    settings = make_settings(
        tmp_path,
        agent_worker_token="worker-secret",
        reroute_api_base_url=base,
        airline_api_base_url=base,
        security_runtime="openshell",
    )
    async with httpx.AsyncClient(base_url=base) as client:
        before = (await client.get("/api/system/runtime")).json()
        assert before["security"]["agent_runtime"] == "policy-mirror" and before["agent"]["worker"]["connected"] is False
        AgentWorker(settings)  # its first call to the control plane carries X-Agent-Security-Runtime: openshell
        after = (await client.get("/api/system/runtime")).json()
        assert after["security"]["runtime"] == "policy-mirror"  # the control plane's own setting is unchanged
        assert after["security"]["agent_runtime"] == "openshell" and after["agent"]["worker"]["connected"] is True
        assert after["security"]["enforced_by"] == "NVIDIA OpenShell sandbox"
        assert (await client.get("/api/security/policy")).json()["agent_runtime"] == "openshell"


async def test_worker_executes_an_operator_approved_waiver(tmp_path):
    """Assist mode across the sandbox boundary: the worker recommends, the control plane re-verifies, a duty manager
    approves, the worker executes the move, and the final report carries the decision."""
    with serve_control_plane(tmp_path, exception_resolution_mode="assist") as (base, _):
        worker = make_worker(base, tmp_path, exception_resolution_mode="assist")
        async with httpx.AsyncClient(base_url=base) as client:
            task = (await client.post("/api/agent/tasks", json={"command": COMMAND})).json()
            assert await worker.run_once() is True
            plan_id = (await client.get(f"/api/agent/tasks/{task['id']}")).json()["plan_id"]
            recs = (await client.get(f"/api/rebooking/plans/{plan_id}/exception-resolutions")).json()["recommendations"]
            assert {r["passenger_id"]: r["verdict"] for r in recs}["P010"] == "PASS_REQUIRES_WAIVER"
            decisions = [{"passenger_id": r["passenger_id"], "decision": "ACCEPT"} for r in recs]
            r = await client.post(
                f"/api/rebooking/plans/{plan_id}/approve",
                json={"exception_decisions": decisions},
                headers={"X-Operator-Id": "dm.park"},
            )
            assert r.status_code == 200, r.text
            assert await worker.run_once() is True
            done = (await client.get(f"/api/agent/tasks/{task['id']}")).json()
    assert done["state"] == "COMPLETED" and done["report"]["rebooked"] == 35
    assert done["report"]["exception_decisions"]["P010"] == "REQUEST_POLICY_WAIVER"
