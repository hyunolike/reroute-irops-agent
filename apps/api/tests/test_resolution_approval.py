"""Assist mode (P3): the operator accepts / modifies / rejects each exception recommendation at approval time.

Recommendations are re-verified by the control plane against fresh inventory, waivers need a duty manager, and
nothing is approved unless every decision verifies.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import httpx
import pytest
from sqlalchemy import select

from app.db.models import Flight
from app.services.airline import AirlineService
from tests.conftest import COMMAND, OPERATOR, build_app, make_settings, run_agent_to_approval

DUTY_MANAGER = {"X-Operator-Id": "dm.park"}
ACCEPT_ALL = [{"passenger_id": p, "decision": "ACCEPT"} for p in ("P010", "P011", "P013", "P014")]


@asynccontextmanager
async def assist(tmp_path, mode="assist"):
    app, c = build_app(make_settings(tmp_path, exception_resolution_mode=mode))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
        task = await run_agent_to_approval(client, c, COMMAND)
        yield client, c, task


async def approve(client, task, decisions, headers=DUTY_MANAGER, **body):
    return await client.post(
        f"/api/rebooking/plans/{task['plan_id']}/approve", json={"exception_decisions": decisions, **body}, headers=headers
    )


async def finish(client, c, task):
    await c.runner.drain()
    done = (await client.get(f"/api/agent/tasks/{task['id']}")).json()
    plan = (await client.get(f"/api/rebooking/plans/{task['plan_id']}")).json()
    return done, {i["passenger_id"]: i for i in plan["items"]}


async def test_console_gets_recommendations_reverified_by_the_control_plane(tmp_path):
    async with assist(tmp_path) as (client, _, task):
        res = (await client.get(f"/api/rebooking/plans/{task['plan_id']}/exception-resolutions")).json()
    recs = {r["passenger_id"]: r for r in res["recommendations"]}
    assert res["mode"] == "assist" and set(recs) == {"P010", "P011", "P013", "P014"}
    assert recs["P010"]["verdict"] == "PASS_REQUIRES_WAIVER" and recs["P010"]["seat"] == {
        "flight_no": "7C1102",
        "cabin": "ECONOMY",
    }
    assert all(
        recs[p]["verdict"] == "PASS" and recs[p]["action"] == "CONFIRM_SOLVER_ASSIGNMENT" for p in ("P011", "P013", "P014")
    )
    assert all(r["item_id"] and r["planner_verdict"] == r["verdict"] for r in recs.values())


async def test_shadow_mode_shows_nothing_and_accepts_no_decisions(tmp_path):
    async with assist(tmp_path, mode="shadow") as (client, _, task):
        res = (await client.get(f"/api/rebooking/plans/{task['plan_id']}/exception-resolutions")).json()
        assert "recommendations" not in res
        r = await approve(client, task, ACCEPT_ALL)
        assert r.status_code == 422 and "assist" in r.json()["detail"]["message"]


async def test_waiver_needs_a_duty_manager_and_nothing_is_approved_without_one(tmp_path):
    async with assist(tmp_path) as (client, _, task):
        r = await approve(client, task, ACCEPT_ALL, headers=OPERATOR)
        assert r.status_code == 403 and r.json()["detail"]["code"] == "WAIVER_AUTHORITY"
        plan = (await client.get(f"/api/rebooking/plans/{task['plan_id']}")).json()
        assert plan["approval"]["status"] == "PENDING"


async def test_duty_manager_accepts_all_and_the_waiver_is_executed_and_audited(tmp_path):
    async with assist(tmp_path) as (client, c, task):
        r = await approve(client, task, ACCEPT_ALL)
        assert r.status_code == 200, r.text
        done, items = await finish(client, c, task)
        audit = (await client.get("/api/audit", params={"limit": 200})).json()["entries"]
        res = (await client.get(f"/api/rebooking/plans/{task['plan_id']}/exception-resolutions")).json()
    report = done["report"]
    assert report["rebooked"] == 35  # 31 auto + 3 confirmed manual reviews + P010 via the waiver
    assert items["P010"]["status"] == "EXECUTED" and items["P010"]["alternative_flight"] == "7C1102"
    assert "policy waiver signed by dm.park" in items["P010"]["reason"]
    assert all(items[p]["status"] == "EXECUTED" for p in ("P011", "P013", "P014"))
    assert report["by_flight"]["7C1102"] == 1
    assert report["exception_decisions"]["P010"] == "REQUEST_POLICY_WAIVER"
    waiver = next(e for e in audit if e["action"] == "policy.waiver")
    assert waiver["agent"] == "dm.park" and waiver["policy"] == "IROP-002" and "P010" in waiver["target"]
    assert res["metrics"]["operator_decisions"] == {"ACCEPT": 4}


async def test_operator_rejects_the_waiver_and_confirms_the_rest(tmp_path):
    decisions = [{"passenger_id": "P010", "decision": "REJECT"}, *ACCEPT_ALL[1:]]
    async with assist(tmp_path) as (client, c, task):
        r = await approve(client, task, decisions, headers=OPERATOR)  # no waiver -> no duty manager needed
        assert r.status_code == 200, r.text
        done, items = await finish(client, c, task)
    assert done["report"]["rebooked"] == 34
    assert items["P010"]["status"] == "NO_FEASIBLE" and items["P010"]["alternative_flight"] is None


async def test_operator_modifies_to_a_refund(tmp_path):
    refund = {
        "passenger_id": "P010",
        "action": "OFFER_REFUND",
        "policy_ids": ["IROP-004"],
        "rationale": "passenger prefers refund",
    }
    decisions = [{"passenger_id": "P010", "decision": "MODIFY", "proposal": refund}]
    async with assist(tmp_path) as (client, c, task):
        r = await approve(client, task, decisions, headers=OPERATOR)
        assert r.status_code == 200, r.text
        decided = r.json()["approval"]["exception_decisions"]
        done, _ = await finish(client, c, task)
    assert decided[0]["source"] == "operator" and decided[0]["decided_by"] == OPERATOR["X-Operator-Id"]
    assert any(f.startswith("Refund offer (operator-approved)") and "[IROP-004]" in f for f in done["report"]["follow_ups"])
    assert done["report"]["rebooked"] == 31  # P011/P013/P014 undecided and unchecked -> held for the operator


async def test_operator_modifications_are_verified_too(tmp_path):
    bad = {"passenger_id": "P013", "action": "REASSIGN_TO_OPTION", "flight_no": "KE701", "rationale": "earlier arrival"}
    async with assist(tmp_path) as (client, _, task):
        r = await approve(client, task, [{"passenger_id": "P013", "decision": "MODIFY", "proposal": bad}], headers=OPERATOR)
        assert r.status_code == 422
        assert r.json()["detail"]["violations"]["P013"][0].startswith("CAPACITY")
        assert (await client.get(f"/api/rebooking/plans/{task['plan_id']}")).json()["approval"]["status"] == "PENDING"


async def test_a_decision_overrides_the_manual_review_checkbox(tmp_path):
    async with assist(tmp_path) as (client, c, task):
        plan = (await client.get(f"/api/rebooking/plans/{task['plan_id']}")).json()
        p011 = next(i["id"] for i in plan["items"] if i["passenger_id"] == "P011")
        r = await approve(
            client, task, [{"passenger_id": "P011", "decision": "REJECT"}], headers=OPERATOR, approved_manual_item_ids=[p011]
        )
        assert r.status_code == 200 and r.json()["approval"]["approved_manual_item_ids"] == []
        _, items = await finish(client, c, task)
    assert items["P011"]["status"] == "HELD_FOR_OPERATOR"


async def test_inventory_that_changed_since_planning_is_caught_before_approval(tmp_path):
    async with assist(tmp_path) as (client, c, task):
        with c.db.session() as s:  # 7C1102 sells out after the agent proposed the waiver
            f = s.scalar(select(Flight).where(Flight.flight_no == "7C1102"))
            f.economy_capacity -= AirlineService(s).get_flight("7C1102").economy_available
            s.commit()
        res = (await client.get(f"/api/rebooking/plans/{task['plan_id']}/exception-resolutions")).json()
        p010 = next(r for r in res["recommendations"] if r["passenger_id"] == "P010")
        assert p010["planner_verdict"] == "PASS_REQUIRES_WAIVER" and p010["verdict"] == "REJECTED"
        assert p010["violations"][0].startswith("CAPACITY")
        r = await approve(client, task, ACCEPT_ALL)
        assert r.status_code == 422 and "P010" in r.json()["detail"]["violations"]


@pytest.mark.parametrize(
    ("decisions", "needle"),
    [
        ([{"passenger_id": "P001", "decision": "ACCEPT"}], "not an exception passenger"),
        ([{"passenger_id": "P010", "decision": "ACCEPT"}, {"passenger_id": "p010", "decision": "REJECT"}], "more than one"),
        ([{"passenger_id": "P010", "decision": "MODIFY"}], "needs the operator's proposal"),
    ],
)
async def test_malformed_decisions_are_refused(tmp_path, decisions, needle):
    async with assist(tmp_path) as (client, _, task):
        r = await approve(client, task, decisions)
    assert r.status_code == 422 and needle in r.json()["detail"]["message"]


async def test_demo_reset_after_an_executed_plan_restores_the_scenario(tmp_path):
    """Recorded recommendations must not block the demo reset (they reference the plan being deleted)."""
    async with assist(tmp_path) as (client, c, task):
        assert (await approve(client, task, ACCEPT_ALL)).status_code == 200
        await finish(client, c, task)
        r = await client.post("/api/demo/reset")
        assert r.status_code == 200, r.text
        again = await run_agent_to_approval(client, c, COMMAND)
        assert again["state"] == "WAITING_APPROVAL"
        res = (await client.get(f"/api/rebooking/plans/{again['plan_id']}/exception-resolutions")).json()
    assert res["metrics"]["coverage"] == 1.0
