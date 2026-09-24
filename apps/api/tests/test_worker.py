"""Remote agent worker (the process meant to run inside an OpenShell sandbox), over real HTTP."""

import socket
import threading
import time

import httpx
import pytest
import uvicorn

from app.agent.worker import AgentWorker
from tests.conftest import COMMAND, OPERATOR, build_app, make_settings


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def live_control_plane(tmp_path):
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    settings = make_settings(
        tmp_path,
        agent_execution="remote",
        agent_worker_token="worker-secret",
        airline_api_base_url=base,
        reroute_api_base_url=base,
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
    yield base, tmp_path
    server.should_exit = True
    t.join(timeout=5)


async def test_worker_runs_task_end_to_end_without_db(live_control_plane):
    base, tmp_path = live_control_plane
    worker_settings = make_settings(
        tmp_path,
        database_url="sqlite:///nonexistent-dir/never-used.db",  # the worker must not need a database
        approval_signing_secret="worker-does-not-know-the-secret",
        agent_worker_token="worker-secret",
        airline_api_base_url=base,
        reroute_api_base_url=base,
    )
    worker = AgentWorker(worker_settings)
    async with httpx.AsyncClient(base_url=base) as client:
        task = (await client.post("/api/agent/tasks", json={"command": COMMAND})).json()
        assert task["state"] == "RECEIVED"  # queued for the worker, not run in-process
        assert await worker.run_once() is True
        task = (await client.get(f"/api/agent/tasks/{task['id']}")).json()
        assert task["state"] == "WAITING_APPROVAL"

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
