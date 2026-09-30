"""LLMOps telemetry (P4): per-turn LLM usage, the task trace, and OTLP export of settled tasks."""

from __future__ import annotations

from contextlib import asynccontextmanager

import httpx
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.observability.otel import build_exporter
from app.providers.llm.mock import MockLLMProvider
from tests.conftest import COMMAND, build_app, make_settings, run_agent_to_approval

ACCEPT_ALL = [{"passenger_id": p, "decision": "ACCEPT"} for p in ("P010", "P011", "P013", "P014")]


class MeteredLLM(MockLLMProvider):
    """The scripted planner, reporting usage and latency like a hosted model; every other turn is tool-calls only."""

    name, model = "metered", "metered-planner"

    def __init__(self):
        self.turns = 0

    async def chat(self, messages, tools=None, *, task_id=None, max_tokens=2048):
        resp = await super().chat(messages, tools, task_id=task_id, max_tokens=max_tokens)
        self.turns += 1
        resp.usage = {"prompt_tokens": 1000, "completion_tokens": 50, "total_tokens": 1050}
        resp.latency_ms = 120.0
        if self.turns % 2 == 0 and resp.tool_calls:
            resp.content = None
        return resp


@asynccontextmanager
async def approved_run(tmp_path, llm=None, span_exporter=None):
    app, c = build_app(make_settings(tmp_path, exception_resolution_mode="assist"))
    if llm:
        c.orchestrator.llm = llm
    if span_exporter is not None:
        c.trace_exporter = build_exporter(None, "reroute-test", c._task_trace, span_exporter=span_exporter)
        c.repo.on_settled = c.trace_exporter.export_task
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
        task = await run_agent_to_approval(client, c, COMMAND)
        r = await client.post(
            f"/api/rebooking/plans/{task['plan_id']}/approve",
            json={"exception_decisions": ACCEPT_ALL},
            headers={"X-Operator-Id": "dm.park"},
        )
        assert r.status_code == 200, r.text
        await c.runner.drain()
        yield client, c, task


async def get_trace(client, task_id):
    return (await client.get(f"/api/agent/tasks/{task_id}/trace")).json()


async def test_every_llm_turn_is_metered_without_extra_timeline_rows(tmp_path):
    llm = MeteredLLM()
    async with approved_run(tmp_path, llm) as (client, _, task):
        events = (await client.get(f"/api/agent/tasks/{task['id']}/events", params={"stream": False})).json()["events"]
        trace = await get_trace(client, task["id"])
    planner_events = [e for e in events if e["type"] == "PLANNER"]
    silent_turns = [e for e in events if e["type"] == "TOOL_CALL" and "llm" in e["detail"]]
    assert silent_turns and len(planner_events) + len(silent_turns) == llm.turns  # one record per turn, no more
    s = trace["summary"]
    assert s["llm_calls"] == llm.turns
    assert (s["input_tokens"], s["output_tokens"]) == (1000 * llm.turns, 50 * llm.turns)
    assert s["llm_latency_ms"] == 120.0 * llm.turns
    llm_span = next(sp for sp in trace["spans"] if sp["kind"] == "llm")
    assert llm_span["attributes"]["gen_ai.request.model"] == "metered-planner"
    assert llm_span["attributes"]["gen_ai.usage.input_tokens"] == 1000


async def test_each_exception_is_one_span_from_analysis_to_operator_decision(tmp_path):
    async with approved_run(tmp_path) as (client, _, task):
        trace = await get_trace(client, task["id"])
    spans = {s["span_id"]: s for s in trace["spans"]}
    root = trace["spans"][0]
    assert root["name"] == "recovery_task" and root["attributes"]["reroute.task.state"] == "COMPLETED"
    for pid in ("P010", "P011", "P013", "P014"):
        exc = spans[f"exception-{pid}"]
        assert exc["parent_id"] == root["span_id"]
        children = sorted(s["name"] for s in trace["spans"] if s["parent_id"] == exc["span_id"])
        assert children == [
            "execute_tool explore_exception_options",
            "execute_tool propose_exception_resolution",
            "operator_decision",
        ]
    p010 = trace["summary"]["exceptions"]["P010"]
    assert p010["final_action"] == "REQUEST_POLICY_WAIVER" and p010["operator_decision"] == "ACCEPT"
    assert p010["prompt_version"].startswith("sp-")
    propose = next(
        s for s in trace["spans"] if s["parent_id"] == "exception-P010" and s["name"].endswith("propose_exception_resolution")
    )
    assert propose["attributes"]["reroute.verifier.verdict"] == "PASS_REQUIRES_WAIVER"
    assert trace["summary"]["tool_calls"] == sum(1 for s in trace["spans"] if s["kind"] == "tool")


async def test_settled_task_is_exported_over_otlp_once(tmp_path):
    exporter = InMemorySpanExporter()
    async with approved_run(tmp_path, span_exporter=exporter) as (client, _, task):
        trace = await get_trace(client, task["id"])
    spans = exporter.get_finished_spans()
    assert len(spans) == len(trace["spans"])  # exported once, when the task completed
    assert len({s.context.trace_id for s in spans}) == 1
    by_name = {s.name: s for s in spans}
    root, exc = by_name["recovery_task"], by_name["exception P010"]
    assert root.attributes["reroute.task.id"] == task["id"] and root.parent is None
    assert exc.parent.span_id == root.context.span_id
    decision = next(
        s for s in spans if s.name == "operator_decision" and s.attributes["reroute.exception.passenger_id"] == "P010"
    )
    assert decision.parent.span_id == exc.context.span_id
    assert decision.attributes["reroute.operator.id"] == "dm.park"
    assert root.resource.attributes["service.name"] == "reroute-test"


async def test_export_failure_never_breaks_the_workflow(tmp_path):
    def broken(_task_id):
        raise RuntimeError("collector down")

    app, c = build_app(make_settings(tmp_path))
    c.trace_exporter = build_exporter(None, "t", broken, span_exporter=InMemorySpanExporter())
    c.repo.on_settled = c.trace_exporter.export_task
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
        task = await run_agent_to_approval(client, c, "ZZ999편 결항 처리해줘.")  # fails -> settles -> export raises
    assert task["state"] == "FAILED" and "collector" not in (task["error"] or "")


def test_exporter_is_off_without_an_endpoint():
    assert build_exporter(None, "reroute", lambda _: None) is None
