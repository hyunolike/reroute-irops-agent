"""End-to-end agent workflow (mock planner) + NVIDIA NIM adapter contract."""

import json

import httpx
import pytest

from app.providers.llm.nim import NvidiaNimProvider
from app.security.governed_http import PolicyViolation
from tests.conftest import OPERATOR, build_app, make_settings, run_agent_to_approval

EXPECTED_STATES = [
    "RECEIVED",
    "ANALYZING_DISRUPTION",
    "FETCHING_PASSENGERS",
    "SEARCHING_ALTERNATIVES",
    "RETRIEVING_POLICIES",
    "OPTIMIZING",
    "GENERATING_PROPOSAL",
    "WAITING_APPROVAL",
]


async def events(client, task_id):
    return (await client.get(f"/api/agent/tasks/{task_id}/events", params={"stream": False})).json()["events"]


async def test_full_workflow_states_and_tools(client, container):
    task = await run_agent_to_approval(client, container)
    evs = await events(client, task["id"])
    states = [e["detail"]["state"] for e in evs if e["type"] == "STATE_CHANGED"]
    assert states == EXPECTED_STATES
    tools = [e["detail"]["tool"] for e in evs if e["type"] == "TOOL_CALL"]
    assert tools[:3] == ["get_disrupted_flight", "get_affected_passengers", "search_alternative_flights"]
    assert tools[-1] == "propose_rebooking"
    assert tools.index("optimize_rebooking") < tools.index("explore_exception_options")
    queries = [
        q
        for e in evs
        if e["type"] == "TOOL_CALL" and e["detail"]["tool"] == "search_rebooking_policy"
        for q in e["detail"]["args"]["queries"]
    ]
    assert len(queries) >= 9
    explored = {
        e["detail"]["args"]["passenger_id"]
        for e in evs
        if e["type"] == "TOOL_CALL" and e["detail"]["tool"] == "explore_exception_options"
    }
    assert explored == {"P010", "P011", "P013", "P014"}  # every exception passenger was analysed
    assert not [e for e in evs if e["type"] in ("TOOL_ERROR", "GUARDRAIL")]

    plan = (await client.get(f"/api/rebooking/plans/{task['plan_id']}")).json()
    assert (
        plan["summary"]["auto_assigned"] == 31 and plan["summary"]["manual_review"] == 3 and plan["summary"]["no_feasible"] == 1
    )
    evidence = {p["policy_id"] for p in plan["policy_evidence"]}
    assert {"IROP-001", "IROP-002", "MCT-002", "VIP-001", "SSR-001"} <= evidence
    cited = {pid for i in plan["items"] for pid in i["policy_ids"]}
    assert cited <= evidence  # every per-passenger reason cites a retrieved policy
    assert "[IROP-001]" in plan["explanation"]


async def test_every_agent_egress_is_policy_checked(client, container):
    task = await run_agent_to_approval(client, container)
    audit = (await client.get("/api/audit", params={"task_id": task["id"]})).json()["entries"]
    egress = [e for e in audit if e["action"] == "network.egress"]
    assert egress and all(e["result"] == "ALLOW" and e["enforced_by"] == "policy-mirror" for e in egress)
    assert {e["tool"] for e in egress} >= {"get_disrupted_flight", "optimize_rebooking", "search_rebooking_policy"}


async def test_approve_completes_with_report(client, container):
    task = await run_agent_to_approval(client, container)
    await client.post(f"/api/rebooking/plans/{task['plan_id']}/approve", json={}, headers=OPERATOR)
    await container.runner.drain()
    done = (await client.get(f"/api/agent/tasks/{task['id']}")).json()
    assert done["state"] == "COMPLETED"
    assert done["report"]["rebooked"] == 31 and done["report"]["held_for_operator"] == 3
    assert any("VIP contact" in f for f in done["report"]["follow_ups"])


async def test_delayed_flight_below_threshold_needs_no_action(client, container):
    task = await run_agent_to_approval(client, container, "KE125 지연됐는데 재배정 필요한지 확인해줘")
    assert task["state"] == "COMPLETED" and task["report"]["outcome"] == "NO_ACTION_REQUIRED"
    assert "RBK-002" in task["report"]["reasoning"]


async def test_unknown_flight_fails_visibly(client, container):
    task = await run_agent_to_approval(client, container, "ZZ999편 결항 처리해줘")
    assert task["state"] == "FAILED" and task["error"]


async def test_sse_stream_replays_events(client, container):
    task = await run_agent_to_approval(client, container)
    await client.post(f"/api/rebooking/plans/{task['plan_id']}/approve", json={}, headers=OPERATOR)
    await container.runner.drain()
    async with client.stream("GET", f"/api/agent/tasks/{task['id']}/events") as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        body = ""
        async for chunk in r.aiter_text():
            body += chunk
            if "event: end" in body:
                break
    assert "event: agent-event" in body and "COMPLETED" in body


# ---------------------------------------------------------------------- NVIDIA NIM adapter
def nim_handler(record):
    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        record.append((req.url.host, req.url.path, req.headers.get("authorization"), body))
        return httpx.Response(
            200,
            json={
                "model": body["model"],
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "Verify the flight first.",
                            "tool_calls": [
                                {
                                    "id": "c1",
                                    "type": "function",
                                    "function": {"name": "get_disrupted_flight", "arguments": '{"flight_no": "KE123"}'},
                                }
                            ],
                        }
                    }
                ],
            },
        )

    return handler


async def test_nim_provider_request_contract(container):
    record = []
    container.http.external_transport = httpx.MockTransport(nim_handler(record))
    nim = NvidiaNimProvider(
        container.http, "nvapi-test", "https://integrate.api.nvidia.com/v1", "nvidia/nemotron-3-super-120b-a12b"
    )
    resp = await nim.chat([{"role": "system", "content": "s"}, {"role": "user", "content": "u"}], container.tools.schemas())
    host, path, auth, body = record[0]
    assert (host, path, auth) == ("integrate.api.nvidia.com", "/v1/chat/completions", "Bearer nvapi-test")
    assert body["tool_choice"] == "auto" and body["tools"][0]["type"] == "function"
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert resp.tool_calls[0].name == "get_disrupted_flight" and resp.tool_calls[0].arguments == {"flight_no": "KE123"}


async def test_llama_nemotron_uses_no_think_tag(container):
    record = []
    container.http.external_transport = httpx.MockTransport(nim_handler(record))
    nim = NvidiaNimProvider(
        container.http, "k", "https://integrate.api.nvidia.com/v1", "nvidia/llama-3.3-nemotron-super-49b-v1.5"
    )
    await nim.chat([{"role": "system", "content": "sys"}, {"role": "user", "content": "u"}])
    assert record[0][3]["messages"][0]["content"].startswith("/no_think")
    assert "chat_template_kwargs" not in record[0][3]


async def test_nim_egress_outside_policy_is_blocked(container):
    nim = NvidiaNimProvider(container.http, "k", "https://evil-llm-proxy.example.com/v1", "nvidia/nemotron-3-super-120b-a12b")
    with pytest.raises(PolicyViolation):
        await nim.chat([{"role": "user", "content": "hi"}])


async def test_agent_falls_back_when_nim_fails(tmp_path):
    app, c = build_app(make_settings(tmp_path))
    c.http.external_transport = httpx.MockTransport(lambda req: httpx.Response(503, text="unavailable"))
    nim = NvidiaNimProvider(c.http, "k", "https://integrate.api.nvidia.com/v1", "nvidia/nemotron-3-super-120b-a12b")
    nim.retry_base_delay = 0
    c.orchestrator.llm = nim
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
        task = await run_agent_to_approval(client, c)
        assert task["state"] == "WAITING_APPROVAL"
        assert "planner_fallback" in task["runtime"]
        evs = await events(client, task["id"])
        assert any(e["type"] == "GUARDRAIL" and "fallback" in e["title"] for e in evs)
