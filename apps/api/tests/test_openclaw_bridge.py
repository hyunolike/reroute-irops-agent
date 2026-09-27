"""Dashboard -> OpenClaw: the operator picks OpenClaw in the command box, the NemoClaw-side bridge claims the task,
OpenClaw attaches to that same task over MCP, and the operator still approves in ReRoute."""

import httpx

from tests.test_mcp import TOKEN, live, mcp_client, tool  # noqa: F401 - `live` is a fixture

AUTH = {"Authorization": f"Bearer {TOKEN}"}
COMMAND = "KE123편이 결항됐어. 재배정안 만들어서 승인 요청까지 해줘."


async def test_openclaw_task_is_queued_for_the_bridge_not_reroutes_worker(live):  # noqa: F811
    base, c = live
    async with httpx.AsyncClient(base_url=base) as h:
        task = (await h.post("/api/agent/tasks", json={"command": COMMAND, "agent": "openclaw"})).json()
        assert task["state"] == "RECEIVED"
        assert task["runtime"]["requested_agent"] == "openclaw" and task["runtime"]["planner"] == "external"
        assert c.repo.claim_pending() is None  # ReRoute's own worker never takes OpenClaw work

        assert (await h.post("/api/bridge/openclaw/claim")).status_code == 401  # bridge needs the token
        claimed = (await h.post("/api/bridge/openclaw/claim", headers=AUTH)).json()
        assert claimed["task"]["id"] == task["id"] and claimed["task"]["command"] == COMMAND
        assert (await h.post("/api/bridge/openclaw/claim", headers=AUTH)).status_code == 204  # claimed once

        rt = (await h.get("/api/system/runtime")).json()
        assert rt["openclaw"]["connected"] is True  # a claim is also a heartbeat


async def test_openclaw_attaches_to_dashboard_task_and_human_still_approves(live):  # noqa: F811
    base, c = live
    async with httpx.AsyncClient(base_url=base) as h:
        tid = (await h.post("/api/agent/tasks", json={"command": COMMAND, "agent": "openclaw"})).json()["id"]
        await h.post("/api/bridge/openclaw/claim", headers=AUTH)

    async with mcp_client(base, "legacy") as client:
        opened = await tool(client, "open_recovery_task", instruction=COMMAND, task_id=tid)
        assert opened["task_id"] == tid  # same task the operator is watching, not a new one
        again = await tool(client, "open_recovery_task", instruction=COMMAND, task_id=tid)
        assert "already being planned" in again["error"]

        flight = await tool(client, "get_disrupted_flight", task_id=tid, flight_no="KE123")
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
        await tool(client, "search_rebooking_policy", task_id=tid, queries=first["coverage"]["suggested_queries"])
        await tool(client, "optimize_rebooking", task_id=tid, flight_no="KE123")
        await tool(client, "propose_rebooking", task_id=tid, flight_no="KE123")

    async with httpx.AsyncClient(base_url=base) as h:
        r = await h.post(
            f"/api/bridge/openclaw/tasks/{tid}/reply",
            json={"text": "Plan proposed; awaiting approval.", "ok": True},
            headers=AUTH,
        )
        assert r.json()["state"] == "WAITING_APPROVAL"
        evs = (await h.get(f"/api/agent/tasks/{tid}/events", params={"stream": False})).json()["events"]
    titles = [e["title"] for e in evs if e["component"] == "external-agent"]
    assert titles[0].startswith("Sent to OpenClaw") and any("took the task" in t for t in titles)
    assert titles[-1] == "OpenClaw: Plan proposed; awaiting approval."


async def test_external_agent_cannot_attach_to_reroutes_own_task(live):  # noqa: F811
    base, c = live
    async with httpx.AsyncClient(base_url=base) as h:
        tid = (await h.post("/api/agent/tasks", json={"command": COMMAND})).json()["id"]  # ReRoute's agent owns it
    async with mcp_client(base, "legacy") as client:
        out = await tool(client, "open_recovery_task", instruction=COMMAND, task_id=tid)
    assert "not handed to an external agent" in out["error"]


async def test_failed_openclaw_run_fails_the_task(live):  # noqa: F811
    base, c = live
    async with httpx.AsyncClient(base_url=base) as h:
        tid = (await h.post("/api/agent/tasks", json={"command": COMMAND, "agent": "openclaw"})).json()["id"]
        await h.post("/api/bridge/openclaw/claim", headers=AUTH)
        t = (
            await h.post(
                f"/api/bridge/openclaw/tasks/{tid}/reply", json={"text": "AI service overloaded", "ok": False}, headers=AUTH
            )
        ).json()
    assert t["state"] == "FAILED" and "overloaded" in t["error"]
