"""ReRoute as an MCP server for external agents (e.g. OpenClaw in NemoClaw) - over real Streamable HTTP."""

import asyncio
import json
import socket
import threading
import time

import httpx
import httpx2
import pytest
import uvicorn
from mcp import Client, types
from mcp.client.streamable_http import streamable_http_client

from tests.conftest import OPERATOR, build_app, make_settings

TOKEN = "mcp-test-token"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def live(tmp_path):
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    settings = make_settings(
        tmp_path,
        mcp_bearer_token=TOKEN,
        airline_api_base_url=base,
        reroute_api_base_url=base,
        mcp_allowed_hosts=f"127.0.0.1:{port}",
    )
    app, container = build_app(settings)
    container.http.transport = None
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.05)
    yield base, container
    server.should_exit = True
    t.join(timeout=5)


def mcp_client(base: str, mode: str = "auto", token: str = TOKEN) -> Client:
    http = httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}", "User-Agent": "openclaw-test/1.0"})
    return Client(
        streamable_http_client(f"{base}/mcp", http_client=http),
        mode=mode,
        client_info=types.Implementation(name="openclaw-test", version="1.0"),
    )


async def tool(client: Client, name: str, **args) -> dict:
    res = await client.call_tool(name, args)
    if res.structured_content is not None:
        data = res.structured_content
        return data.get("result", data) if set(data) == {"result"} else data
    return json.loads(res.content[0].text)


async def test_mcp_requires_bearer_token(live):
    base, c = live
    async with httpx.AsyncClient() as h:
        r = await h.post(f"{base}/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert r.status_code == 401
    assert any(e.action == "mcp.connect" and e.result == "DENY" for e in c.audit.list())


@pytest.mark.parametrize("mode", ["legacy", "auto"])
async def test_tool_surface_has_no_approval_power(live, mode):
    base, _ = live
    async with mcp_client(base, mode) as client:
        names = {t.name for t in (await client.list_tools()).tools}
    assert {
        "open_recovery_task",
        "get_disrupted_flight",
        "search_rebooking_policy",
        "optimize_rebooking",
        "explore_exception_options",
        "propose_rebooking",
        "finish_without_action",
        "delegate_recovery",
        "get_recovery_status",
        "log_reasoning",
    } <= names
    assert not {n for n in names if any(w in n for w in ("approve", "reject", "execute"))}


async def test_external_agent_drives_full_workflow_with_same_guardrails(live):
    base, c = live
    async with mcp_client(base, "legacy") as client:
        task = await tool(client, "open_recovery_task", instruction="KE123 was cancelled - rebook the passengers")
        tid = task["task_id"]
        await tool(client, "log_reasoning", task_id=tid, note="Plan: verify flight, load pax, find flights, policies, optimize.")
        flight = await tool(client, "get_disrupted_flight", task_id=tid, flight_no="KE123")
        assert flight["status"] == "CANCELLED"

        premature = await tool(client, "optimize_rebooking", task_id=tid, flight_no="KE123")  # same guardrail as inside
        assert "precondition failed" in premature["error"] and "get_affected_passengers" in premature["next_step_hint"]

        await tool(client, "get_affected_passengers", task_id=tid, flight_no="KE123")
        await tool(
            client,
            "search_alternative_flights",
            task_id=tid,
            origin="ICN",
            destination="NRT",
            departure_date=flight["departure_date"],
        )
        first = await tool(client, "search_rebooking_policy", task_id=tid, queries=["minimum connection time NRT"])
        assert first["coverage"]["missing"]
        second = await tool(client, "search_rebooking_policy", task_id=tid, queries=first["coverage"]["suggested_queries"])
        assert second["coverage"]["missing"] == []
        opt = await tool(client, "optimize_rebooking", task_id=tid, flight_no="KE123")
        assert opt["summary"]["auto_assigned"] == 31
        p010 = await tool(client, "explore_exception_options", task_id=tid, passenger_id="P010")
        assert any(o["flight_no"] == "7C1102" for o in p010["options"])
        proposed = await tool(client, "propose_rebooking", task_id=tid, flight_no="KE123")
        assert "human operator must approve" in proposed["next"]

        after = await tool(client, "optimize_rebooking", task_id=tid, flight_no="KE123")
        assert "error" in after  # the session is closed once a plan awaits approval
        status = await tool(client, "get_recovery_status", task_id=tid)
        assert status["state"] == "WAITING_APPROVAL" and status["plan"]["approval"] == "PENDING"

    # Only a human can approve - through the operator API, not MCP
    async with httpx.AsyncClient(base_url=base) as h:
        await h.post(f"/api/rebooking/plans/{status['plan']['plan_id']}/approve", json={}, headers=OPERATOR)
        for _ in range(100):
            t = (await h.get(f"/api/agent/tasks/{tid}")).json()
            if t["state"] == "COMPLETED":
                break
            await asyncio.sleep(0.05)
        assert t["state"] == "COMPLETED" and t["report"]["rebooked"] == 31
        assert t["runtime"]["planner"] == "external" and "openclaw-test" in t["runtime"]["planner_client"]
        evs = (await h.get(f"/api/agent/tasks/{tid}/events", params={"stream": False})).json()["events"]
    assert any(e["component"] == "external-agent" and e["type"] == "PLANNER" for e in evs)
    assert any(e["type"] == "GUARDRAIL" and "optimize_rebooking blocked" in e["title"] for e in evs)
    assert any(e.action == "mcp.tool_call" and e.tool == "mcp.propose_rebooking" for e in c.audit.list(limit=500))


async def test_finish_without_action_only_when_justified(live):
    base, _ = live
    async with mcp_client(base) as client:
        tid = (await tool(client, "open_recovery_task", instruction="KE123 cancelled"))["task_id"]
        await tool(client, "get_disrupted_flight", task_id=tid, flight_no="KE123")
        refused = await tool(client, "finish_without_action", task_id=tid, reason="nothing to do")
        assert "error" in refused and "get_affected_passengers" in refused["next_step_hint"]

        tid2 = (await tool(client, "open_recovery_task", instruction="KE125 delayed"))["task_id"]
        await tool(client, "get_disrupted_flight", task_id=tid2, flight_no="KE125")
        early = await tool(client, "finish_without_action", task_id=tid2, reason="short delay")
        assert "rebooking threshold" in early["next_step_hint"]
        await tool(client, "search_rebooking_policy", task_id=tid2, queries=["rebooking threshold for delayed flights"])
        done = await tool(client, "finish_without_action", task_id=tid2, reason="45 < 180 min [RBK-002]")
        assert done == {"state": "COMPLETED", "outcome": "NO_ACTION_REQUIRED"}


async def test_delegate_to_reroute_agent(live):
    base, _ = live
    async with mcp_client(base) as client:
        tid = (await tool(client, "delegate_recovery", instruction="KE123편 결항 처리해줘"))["task_id"]
        for _ in range(100):
            status = await tool(client, "get_recovery_status", task_id=tid)
            if status["state"] == "WAITING_APPROVAL":
                break
            await asyncio.sleep(0.1)
    assert status["plan"]["summary"]["no_feasible"] == 1
    assert any(e["passenger"].startswith("강도윤") for e in status["plan"]["exceptions"])


async def test_wrong_token_is_rejected(live):
    base, _ = live
    async with httpx.AsyncClient() as h:
        r = await h.post(
            f"{base}/mcp", headers={"Authorization": "Bearer wrong"}, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        )
    assert r.status_code == 401 and r.json() == {"error": "unauthorized"}
