"""LLM-mode agent: provider selection, robustness to real model behaviour, and recovery via guidance."""

import json

import httpx
import pytest

from app.providers.llm.base import LLMProvider, LLMResponse, ToolCall
from app.providers.llm.factory import build_llm
from app.providers.llm.mock import MockLLMProvider
from app.providers.llm.nim import NvidiaNimProvider, parse_text_tool_calls, split_thinking
from tests.conftest import COMMAND, build_app, make_settings, run_agent_to_approval

TOOLS = {"get_disrupted_flight", "optimize_rebooking", "search_rebooking_policy"}


# ---------------------------------------------------------------------- provider selection
def test_auto_uses_nemotron_when_key_present(tmp_path, container):
    llm, reason = build_llm(make_settings(tmp_path, llm_provider="auto", nvidia_api_key="nvapi-x"), container.http)
    assert isinstance(llm, NvidiaNimProvider) and "Nemotron" in reason


def test_auto_without_key_falls_back_with_visible_reason(tmp_path, container):
    llm, reason = build_llm(make_settings(tmp_path, llm_provider="auto", nvidia_api_key=None), container.http)
    assert isinstance(llm, MockLLMProvider) and "NVIDIA_API_KEY" in reason
    llm, reason = build_llm(make_settings(tmp_path, llm_provider="mock", nvidia_api_key="nvapi-x"), container.http)
    assert isinstance(llm, MockLLMProvider)


async def test_runtime_reports_llm_reason(client):
    rt = (await client.get("/api/system/runtime")).json()
    assert rt["llm"]["nvidia"] is False and rt["llm"]["reason"]


# ---------------------------------------------------------------------- real-model output variants
@pytest.mark.parametrize(
    "content",
    [
        '<TOOLCALL>[{"name": "get_disrupted_flight", "arguments": {"flight_no": "KE123"}}]</TOOLCALL>',
        'Checking first.\n<tool_call>{"name": "get_disrupted_flight", "arguments": {"flight_no": "KE123"}}</tool_call>',
        '```json\n{"name": "get_disrupted_flight", "arguments": {"flight_no": "KE123"}}\n```',
        '{"function": {"name": "get_disrupted_flight", "arguments": "{\\"flight_no\\": \\"KE123\\"}"}}',
    ],
)
def test_text_tool_calls_are_parsed(content):
    calls, _ = parse_text_tool_calls(content, TOOLS)
    assert [(c.name, c.arguments) for c in calls] == [("get_disrupted_flight", {"flight_no": "KE123"})]


def test_unknown_or_plain_text_is_not_a_tool_call():
    assert parse_text_tool_calls('<TOOLCALL>[{"name": "rm_rf", "arguments": {}}]</TOOLCALL>', TOOLS)[0] == []
    calls, content = parse_text_tool_calls("KE125 delay is below the threshold [RBK-002].", TOOLS)
    assert calls == [] and "RBK-002" in content


def test_think_blocks_are_separated():
    visible, thinking = split_thinking("<think>compare delay with threshold</think>No action needed.")
    assert visible == "No action needed." and thinking == "compare delay with threshold"
    visible, thinking = split_thinking("internal notes</think>Answer")
    assert visible == "Answer" and thinking == "internal notes"


async def test_nim_retries_transient_errors(container):
    calls = []

    def handler(req):
        calls.append(1)
        if len(calls) < 3:
            return httpx.Response(503 if len(calls) == 1 else 429, text="busy")
        return httpx.Response(200, json={"model": "m", "choices": [{"message": {"content": "ok"}}]})

    container.http.external_transport = httpx.MockTransport(handler)
    nim = NvidiaNimProvider(container.http, "k", "https://integrate.api.nvidia.com/v1", "nvidia/nemotron-3-super-120b-a12b")
    nim.retry_base_delay = 0
    assert (await nim.chat([{"role": "user", "content": "hi"}])).content == "ok"
    assert len(calls) == 3


async def test_nim_backoff_honours_retry_after_and_cap(container, monkeypatch):
    sleeps = []

    async def fake_sleep(s):
        sleeps.append(s)

    monkeypatch.setattr("app.providers.llm.nim.asyncio.sleep", fake_sleep)
    monkeypatch.setattr("app.providers.llm.nim.random.uniform", lambda a, b: 1.0)
    responses = iter(
        [
            httpx.Response(429, headers={"Retry-After": "5"}, text="slow down"),
            httpx.Response(500, text="boom"),
            httpx.Response(429, headers={"Retry-After": "120"}, text="slow down"),
            httpx.Response(200, json={"model": "m", "choices": [{"message": {"content": "ok"}}]}),
        ]
    )
    container.http.external_transport = httpx.MockTransport(lambda req: next(responses))
    nim = NvidiaNimProvider(container.http, "k", "https://integrate.api.nvidia.com/v1", "nvidia/nemotron-3-super-120b-a12b")
    assert (await nim.chat([{"role": "user", "content": "hi"}])).content == "ok"
    assert sleeps == [5.0, 2.0, 8.0]  # Retry-After, then the exponential schedule, capped at retry_max_delay


async def test_nim_text_tool_call_end_to_end(container):
    def handler(req):
        return httpx.Response(
            200,
            json={
                "model": "m",
                "choices": [
                    {
                        "message": {
                            "content": '<think>verify first</think>Plan: 1) verify\n<TOOLCALL>[{"name": "get_disrupted_flight", "arguments": {"flight_no": "KE123"}}]</TOOLCALL>'
                        }
                    }
                ],
            },
        )

    container.http.external_transport = httpx.MockTransport(handler)
    nim = NvidiaNimProvider(container.http, "k", "https://integrate.api.nvidia.com/v1", "nvidia/nemotron-3-super-120b-a12b")
    resp = await nim.chat([{"role": "user", "content": "KE123"}], container.tools.schemas())
    assert resp.tool_calls[0].name == "get_disrupted_flight"
    assert resp.content == "Plan: 1) verify" and resp.reasoning == "verify first"


def test_policy_tool_accepts_single_query_variants():
    from app.tools.knowledge import PolicyArgs

    assert PolicyArgs.model_validate({"query": "minimum connection time"}).queries == ["minimum connection time"]
    assert PolicyArgs.model_validate({"queries": "vip priority"}).queries == ["vip priority"]


# ---------------------------------------------------------------------- an imperfect, realistic model
class RealisticLLM(LLMProvider):
    """Behaves like a real function-calling model: one tool per turn, a premature call, a wrong argument
    shape, reads tool feedback (coverage) and stops once before finishing."""

    name = "fake-nemotron"
    nvidia = True
    model = "fake/nemotron"

    def __init__(self):
        self.turn = 0
        self.stopped_once = False

    @staticmethod
    def last_tool(messages):
        return json.loads(next(m["content"] for m in reversed(messages) if m["role"] == "tool"))

    async def chat(self, messages, tools=None, *, task_id=None, max_tokens=2048):
        if not tools:
            return LLMResponse(content="**상황 요약**\n- KE123 결항 [IROP-001]", model=self.model)
        self.turn += 1
        t = self.turn

        def call(name, **args):
            return LLMResponse(
                content=f"step {t}: {name}", model=self.model, tool_calls=[ToolCall(f"c{t}", name, args, json.dumps(args))]
            )

        if t == 1:
            return call("get_disrupted_flight", flight_no="ke123")
        if t == 2:
            return call("optimize_rebooking", flight_no="KE123")  # premature -> guardrail error
        if t == 3:
            assert "precondition failed" in self.last_tool(messages)["error"]
            return call("get_affected_passengers", flight_no="KE123")
        if t == 4:
            return call("search_alternative_flights", origin="ICN", destination="NRT", departure_date="2026-10-15")
        if t == 5:
            return call("search_rebooking_policy", query="minimum connection time")  # alias + single query
        if t == 6:
            coverage = self.last_tool(messages)["coverage"]
            assert coverage["missing"], "tool must tell the model what is still missing"
            return call("search_rebooking_policy", queries=coverage["suggested_queries"])
        if t == 7:
            assert self.last_tool(messages)["coverage"]["missing"] == []
            return call("optimize_rebooking", flight_no="KE123")
        if t == 8 and not self.stopped_once:
            self.stopped_once = True
            return LLMResponse(content="최적화가 끝났습니다.", model=self.model)  # stops early -> guidance
        last_user = next(m["content"] for m in reversed(messages) if m["role"] == "user")
        if "propose_rebooking" in last_user and t == 9:
            return call("explore_exception_options", passenger_id="p010")
        return call("propose_rebooking", flight_no="KE123")


async def test_realistic_llm_completes_with_guidance_not_fallback(tmp_path):
    app, c = build_app(make_settings(tmp_path))
    c.orchestrator.llm = RealisticLLM()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
        task = await run_agent_to_approval(client, c, COMMAND)
        assert task["state"] == "WAITING_APPROVAL"
        assert "planner_fallback" not in (task["runtime"] or {})
        evs = (await client.get(f"/api/agent/tasks/{task['id']}/events", params={"stream": False})).json()["events"]
        guardrails = [e["title"] for e in evs if e["type"] == "GUARDRAIL"]
        assert any("optimize_rebooking blocked" in g for g in guardrails)
        assert any("next: call propose_rebooking" in g for g in guardrails)
        assert all(e["component"] == "nemotron" for e in evs if e["type"] == "PLANNER")
        explore = next(e for e in evs if e["type"] == "TOOL_RESULT" and e["detail"].get("tool") == "explore_exception_options")
        assert "7C1102" in explore["title"]  # policy-blocked but operationally possible option surfaced
        plan = (await client.get(f"/api/rebooking/plans/{task['plan_id']}")).json()
        assert plan["summary"]["auto_assigned"] == 31  # the solver, not the LLM, decided


class StubbornLLM(LLMProvider):
    name, nvidia, model = "stubborn", True, "fake/stubborn"

    async def chat(self, messages, tools=None, *, task_id=None, max_tokens=2048):
        if not any(m["role"] == "tool" for m in messages):
            return LLMResponse(
                content="Checking.",
                model=self.model,
                tool_calls=[ToolCall("c0", "get_disrupted_flight", {"flight_no": "KE123"}, "")],
            )
        return LLMResponse(content="I think we are done.", model=self.model)


async def test_model_that_keeps_stopping_is_replaced_after_guidance(tmp_path):
    app, c = build_app(make_settings(tmp_path))
    c.orchestrator.llm = StubbornLLM()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
        task = await run_agent_to_approval(client, c, COMMAND)
        assert task["state"] == "WAITING_APPROVAL"
        evs = (await client.get(f"/api/agent/tasks/{task['id']}/events", params={"stream": False})).json()["events"]
        titles = [e["title"] for e in evs if e["type"] == "GUARDRAIL"]
        assert sum("Workflow not finished" in t for t in titles) == 2
        assert any("deterministic planner takes over" in t for t in titles)


async def test_delayed_flight_requires_policy_before_stopping(tmp_path):
    """A model that stops right after seeing DELAYED is told to check the threshold policy first."""

    class Hasty(LLMProvider):
        name, nvidia, model = "hasty", True, "fake/hasty"
        turn = 0

        async def chat(self, messages, tools=None, *, task_id=None, max_tokens=2048):
            self.turn += 1
            if self.turn == 1:
                return LLMResponse(
                    content="check",
                    model=self.model,
                    tool_calls=[ToolCall("a", "get_disrupted_flight", {"flight_no": "KE125"}, "")],
                )
            if self.turn == 2:
                return LLMResponse(content="Only 45 minutes, nothing to do.", model=self.model)
            if self.turn == 3:
                assert "rebooking threshold" in messages[-1]["content"]
                return LLMResponse(
                    content="searching",
                    model=self.model,
                    tool_calls=[
                        ToolCall("b", "search_rebooking_policy", {"queries": ["rebooking threshold for delayed flights"]}, "")
                    ],
                )
            return LLMResponse(content="45 min < 180 min threshold [RBK-002]; no re-accommodation.", model=self.model)

    app, c = build_app(make_settings(tmp_path))
    c.orchestrator.llm = Hasty()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
        task = await run_agent_to_approval(client, c, "KE125 지연 확인해줘")
        assert task["state"] == "COMPLETED" and task["report"]["outcome"] == "NO_ACTION_REQUIRED"
        assert "RBK-002" in task["report"]["policies"]


async def test_evaluation_harness_scenarios_pass(tmp_path, monkeypatch):
    """The scorecard used against real Nemotron must itself be correct (checked here with the scripted planner)."""
    from app.agent import evaluate

    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("PROJECT_ROOT", str(__import__("tests.conftest", fromlist=["ROOT"]).ROOT))
    results = [await evaluate.run_scenario(sc, tmp_path, {"_env_file": None}) for sc in evaluate.SCENARIOS]
    assert [r["passed"] for r in results] == [True] * len(evaluate.SCENARIOS), results


async def test_exception_analysis_is_operationally_correct(client, container):
    task = await run_agent_to_approval(client, container, COMMAND)
    evs = (await client.get(f"/api/agent/tasks/{task['id']}/events", params={"stream": False})).json()["events"]
    by_pid = {
        e["detail"]["passenger"]["passenger_id"]: e
        for e in evs
        if e["type"] == "TOOL_RESULT" and e["detail"].get("tool") == "explore_exception_options"
    }
    # P011 keeps its own KE701 seat: that option is feasible, not "no seat left"
    p011 = {o["flight_no"]: o for o in by_pid["P011"]["detail"]["options"]}
    assert p011["KE701"]["feasible_under_policy"] is True
    # P010: 7C1102 is the only policy-only blocked option; KE2101 lands at HND, the onward flight leaves NRT
    assert "7C1102" in by_pid["P010"]["title"] and "KE2101" not in by_pid["P010"]["title"]
    p010 = {o["flight_no"]: o for o in by_pid["P010"]["detail"]["options"]}
    assert any("airport change" in b for b in p010["KE2101"]["blocked_by"])
