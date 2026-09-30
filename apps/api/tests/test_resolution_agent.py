"""Exception resolution inside the agent loop (P2, shadow mode): the tool, the retry budget, persistence and isolation."""

from __future__ import annotations

from contextlib import asynccontextmanager

import httpx

from app.agent.prompts import prompt_version, system_prompt
from app.providers.llm.base import LLMResponse
from app.providers.llm.mock import MockLLMProvider
from tests.conftest import COMMAND, OPERATOR, build_app, make_settings, run_agent_to_approval


async def events(client, task_id):
    return (await client.get(f"/api/agent/tasks/{task_id}/events", params={"stream": False})).json()["events"]


async def resolutions(client, plan_id):
    return (await client.get(f"/api/rebooking/plans/{plan_id}/exception-resolutions")).json()


class ScriptedResolver(MockLLMProvider):
    """The scripted planner, except that exception recommendations follow `script` (one list of calls per turn).

    A step may be a function of the latest tool result per passenger (`last`: passenger_id -> result).
    """

    name, model = "scripted", "scripted-resolver"

    def __init__(self, script):
        self.script = list(script)

    def _resolve_exceptions(self, called, call):
        if not self.script:
            return None
        # later results overwrite earlier ones: the latest result per passenger
        last = {e["args"]["passenger_id"].upper(): self._last_json([e]) for e in called.get("propose_exception_resolution", [])}
        step = self.script.pop(0)
        proposals = step(last) if callable(step) else step
        return LLMResponse(
            content="recommending", tool_calls=[call("propose_exception_resolution", **p) for p in proposals], model=self.model
        )


@asynccontextmanager
async def run_with(tmp_path, llm, **settings):
    tmp_path.mkdir(parents=True, exist_ok=True)
    app, c = build_app(make_settings(tmp_path, **settings))
    c.orchestrator.llm = llm
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
        yield client, c, await run_agent_to_approval(client, c, COMMAND)


# ------------------------------------------------------------------ scripted planner (demo mode)
async def test_demo_run_recommends_every_exception_and_records_provenance(client, container):
    task = await run_agent_to_approval(client, container)
    res = await resolutions(client, task["plan_id"])
    m = res["metrics"]
    assert m["coverage"] == 1.0 and m["first_pass_accept_rate"] == 1.0 and m["violations"] == {}
    assert m["actions"] == {
        "P010": "REQUEST_POLICY_WAIVER",
        "P011": "CONFIRM_SOLVER_ASSIGNMENT",
        "P013": "CONFIRM_SOLVER_ASSIGNMENT",
        "P014": "CONFIRM_SOLVER_ASSIGNMENT",
    }
    assert m["waivers_requested"] == ["P010"]
    p010 = next(a for a in res["attempts"] if a["passenger_id"] == "P010")
    assert p010["proposal"]["flight_no"] == "7C1102" and p010["required_role"] == "duty_manager"
    assert {a["planner"] for a in res["attempts"]} == {"mock-llm/scripted-planner-v1"}
    assert {a["prompt_version"] for a in res["attempts"]} == {prompt_version(system_prompt(True))}
    assert {a["mode"] for a in res["attempts"]} == {"shadow"}
    assert not [e for e in await events(client, task["id"]) if e["type"] in ("TOOL_ERROR", "GUARDRAIL")]


async def test_shadow_mode_does_not_change_what_the_operator_sees_or_approves(tmp_path):
    views = {}
    for mode in ("shadow", "off"):
        async with run_with(tmp_path / mode, MockLLMProvider(), exception_resolution_mode=mode) as (client, c, task):
            plan = (await client.get(f"/api/rebooking/plans/{task['plan_id']}")).json()
            await client.post(f"/api/rebooking/plans/{task['plan_id']}/approve", json={}, headers=OPERATOR)
            await c.runner.drain()
            done = (await client.get(f"/api/agent/tasks/{task['id']}")).json()
            tools = {e["detail"]["tool"] for e in await events(client, task["id"]) if e["type"] == "TOOL_CALL"}
            views[mode] = {
                "explanation": plan["explanation"],
                "items": [{k: v for k, v in i.items() if k != "id"} for i in plan["items"]],
                "report": {k: v for k, v in done["report"].items() if k != "plan_id"},
                "attempts": (await resolutions(client, task["plan_id"]))["attempts"],
                "tools": tools,
            }
    assert views["shadow"]["explanation"] == views["off"]["explanation"]
    assert views["shadow"]["items"] == views["off"]["items"]
    assert views["shadow"]["report"] == views["off"]["report"]
    assert "propose_exception_resolution" in views["shadow"]["tools"]
    assert "propose_exception_resolution" not in views["off"]["tools"] and views["off"]["attempts"] == []
    assert "explore_exception_options" in views["off"]["tools"]  # analysis still happens when resolution is off


def test_prompt_only_mentions_the_tool_when_it_is_registered(tmp_path):
    (tmp_path / "on").mkdir()
    (tmp_path / "off").mkdir()
    _, on = build_app(make_settings(tmp_path / "on"))
    _, off = build_app(make_settings(tmp_path / "off", exception_resolution_mode="off"))
    assert "propose_exception_resolution" in on.orchestrator.system_prompt
    assert "propose_exception_resolution" not in off.orchestrator.system_prompt
    assert on.orchestrator.prompt_version != off.orchestrator.prompt_version


# ------------------------------------------------------------------ a model that gets it wrong first
def waiver_p010(**kw):
    return {
        "passenger_id": "P010",
        "action": "REQUEST_POLICY_WAIVER",
        "flight_no": "7C1102",
        "policy_ids": ["IROP-002"],
        "rationale": "r",
        **kw,
    }


async def test_rejected_proposal_is_corrected_once_then_the_budget_is_spent(tmp_path):
    def correct(last):
        assert last["P010"]["verdict"] == "REJECTED" and last["P010"]["violations"][0].startswith("UNKNOWN_FLIGHT")
        assert "correct the proposal once" in last["P010"]["next"]
        assert last["P011"]["verdict"] == "REJECTED" and last["P011"]["violations"][0].startswith("CHECKLIST")
        return [
            waiver_p010(),
            {"passenger_id": "P011", "action": "CONFIRM_SOLVER_ASSIGNMENT", "checklist": ["UA902 risk"], "rationale": "r"},
        ]

    llm = ScriptedResolver(
        [
            [  # first try: an invented flight and a confirmation without a checklist
                {"passenger_id": "P010", "action": "REASSIGN_TO_OPTION", "flight_no": "KE999", "rationale": "r"},
                {"passenger_id": "P011", "action": "CONFIRM_SOLVER_ASSIGNMENT", "rationale": "r"},
            ],
            correct,
            [waiver_p010()],  # a third attempt is refused
            [{"passenger_id": "P001", "action": "OFFER_REFUND", "rationale": "r"}],  # never explored (auto-assigned)
        ]
    )
    async with run_with(tmp_path, llm) as (client, _, task):
        assert task["state"] == "WAITING_APPROVAL"
        errors = [e["title"] for e in await events(client, task["id"]) if e["type"] == "TOOL_ERROR"]
        assert any("P010: 2 attempts already made" in t for t in errors)
        assert any("explore_exception_options(passenger_id=P001) first" in t for t in errors)
        res = await resolutions(client, task["plan_id"])
    m = res["metrics"]
    assert m["first_pass_accept_rate"] == 0.0 and m["coverage"] == 0.5
    assert m["violations"] == {"UNKNOWN_FLIGHT": 1, "CHECKLIST": 1}
    assert m["without_recommendation"] == ["P013", "P014"]
    p010 = [(a["attempt"], a["verdict"], a["final"]) for a in res["attempts"] if a["passenger_id"] == "P010"]
    assert p010 == [(1, "REJECTED", False), (2, "PASS_REQUIRES_WAIVER", True)]
    assert {a["planner"] for a in res["attempts"]} == {"scripted/scripted-resolver"}


async def test_seats_already_recommended_are_protected(tmp_path):
    # KE701 economy is full; P011 leaving it (waiver to 7C1102) frees exactly one seat, which P013 takes
    llm = ScriptedResolver(
        [
            [waiver_p010(passenger_id="P011")],
            [{"passenger_id": "P013", "action": "REASSIGN_TO_OPTION", "flight_no": "KE701", "rationale": "r"}],
            # P014 wants the same seat; P011 changes its mind and wants its KE701 seat back
            [{"passenger_id": "P014", "action": "REASSIGN_TO_OPTION", "flight_no": "KE701", "rationale": "r"}],
            [{"passenger_id": "P011", "action": "CONFIRM_SOLVER_ASSIGNMENT", "checklist": ["UA902 risk"], "rationale": "r"}],
        ]
    )
    async with run_with(tmp_path, llm) as (client, _, task):
        res = await resolutions(client, task["plan_id"])
    final = {a["passenger_id"]: (a["action"], a["verdict"]) for a in res["attempts"] if a["final"]}
    assert final == {"P011": ("REQUEST_POLICY_WAIVER", "PASS_REQUIRES_WAIVER"), "P013": ("REASSIGN_TO_OPTION", "PASS")}
    rejected = {a["passenger_id"]: a["violations"] for a in res["attempts"] if a["verdict"] == "REJECTED"}
    assert [v["code"] for v in rejected["P014"]] == ["CAPACITY"]
    assert any("already recommended for P013" in v["message"] for v in rejected["P011"])
