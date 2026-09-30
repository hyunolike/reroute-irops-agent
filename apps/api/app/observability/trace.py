"""One recovery task as a trace: LLM calls, tool calls, and one span per exception passenger that follows it from
analysis through the planner's attempts and the verifier's verdicts to the operator's decision.

Built after the fact from what is already persisted (the event log, recorded recommendations, the approval), so it
works the same whether the agent ran in-process, in a sandboxed worker, or as an external MCP planner. Attribute
names follow the OpenTelemetry GenAI semantic conventions (`gen_ai.*`) so the trace maps onto Langfuse, Phoenix or
any OTLP backend without translation; ReRoute-specific attributes use the `reroute.*` namespace.
"""

from __future__ import annotations

from collections import defaultdict, deque
from datetime import UTC, datetime, timedelta
from typing import Any

EXCEPTION_TOOLS = ("explore_exception_options", "propose_exception_resolution")


def _ts(value: str | None) -> datetime | None:
    if not value:
        return None
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _span(span_id: str, parent: str | None, name: str, kind: str, start: datetime, end: datetime, **attrs: Any) -> dict:
    return {
        "span_id": span_id,
        "parent_id": parent,
        "name": name,
        "kind": kind,
        "start": _iso(start),
        "end": _iso(max(start, end)),
        "duration_ms": round(max(0.0, (end - start).total_seconds() * 1000), 1),
        "status": attrs.pop("status", "OK"),
        "attributes": {k: v for k, v in attrs.items() if v is not None},
    }


def build_trace(
    task: dict[str, Any], events: list[dict[str, Any]], attempts: list[dict[str, Any]], approval: dict[str, Any] | None
) -> dict[str, Any]:
    root_id = f"task-{task['id']}"
    start = _ts(task["created_at"]) or datetime.now(UTC)
    times = [_ts(e["created_at"]) for e in events] + [_ts((approval or {}).get("approved_at"))]
    end = max([t for t in times if t] or [start])
    spans: list[dict[str, Any]] = []
    exc_children: dict[str, list[dict[str, Any]]] = defaultdict(list)

    # ---------------------------------------------------------------- LLM calls
    llm_spans = []
    for e in events:
        llm = (e.get("detail") or {}).get("llm")
        if not llm:
            continue
        t_end = _ts(e["created_at"]) or start
        t_start = t_end - timedelta(milliseconds=llm.get("latency_ms") or 0)
        s = _span(
            f"llm-{e['seq']}",
            root_id,
            f"{llm['operation']} {llm['model']}",
            "llm",
            t_start,
            t_end,
            **{
                "gen_ai.operation.name": "chat",
                "gen_ai.system": llm.get("provider"),
                "gen_ai.request.model": llm.get("model"),
                "gen_ai.usage.input_tokens": llm.get("input_tokens"),
                "gen_ai.usage.output_tokens": llm.get("output_tokens"),
                "reroute.llm.purpose": llm["operation"],
                "reroute.llm.turn": llm.get("turn"),
                "reroute.llm.tool_calls": llm.get("tool_calls") or None,
            },
        )
        llm_spans.append(s)
    spans += llm_spans

    # ---------------------------------------------------------------- tool calls (sequential: pair FIFO per tool)
    open_calls: dict[str, deque] = defaultdict(deque)
    tool_spans = []
    for e in events:
        tool = (e.get("detail") or {}).get("tool")
        if e["type"] == "TOOL_CALL":
            open_calls[tool].append(e)
        elif e["type"] in ("TOOL_RESULT", "TOOL_ERROR") and open_calls[tool]:
            call = open_calls[tool].popleft()
            detail = e.get("detail") or {}
            pid = str((call["detail"].get("args") or {}).get("passenger_id") or "").strip().upper() or None
            s = _span(
                f"tool-{call['seq']}",
                root_id,
                f"execute_tool {tool}",
                "tool",
                _ts(call["created_at"]) or start,
                _ts(e["created_at"]) or start,
                status="ERROR" if e["type"] == "TOOL_ERROR" else "OK",
                **{
                    "gen_ai.operation.name": "execute_tool",
                    "gen_ai.tool.name": tool,
                    "reroute.component": e.get("component"),
                    "reroute.result": e.get("title"),
                    "reroute.exception.passenger_id": pid if tool in EXCEPTION_TOOLS else None,
                    "reroute.verifier.verdict": detail.get("verdict"),
                    "reroute.verifier.violations": detail.get("violations") or None,
                    "reroute.resolution.action": detail.get("action"),
                },
            )
            tool_spans.append(s)
            if pid and tool in EXCEPTION_TOOLS:
                exc_children[pid].append(s)
    spans += tool_spans

    # ---------------------------------------------------------------- operator decisions (assist mode)
    decided_at = _ts((approval or {}).get("approved_at")) or end
    for d in (approval or {}).get("exception_decisions") or []:
        s = _span(
            f"decision-{d['passenger_id']}",
            None,
            "operator_decision",
            "operator",
            decided_at,
            decided_at,
            **{
                "reroute.exception.passenger_id": d["passenger_id"],
                "reroute.operator.decision": d["decision"],
                "reroute.operator.id": d.get("decided_by"),
                "reroute.operator.source": d.get("source"),
                "reroute.resolution.action": d.get("action"),
                "reroute.resolution.required_role": d.get("required_role"),
            },
        )
        exc_children[d["passenger_id"]].append(s)
        spans.append(s)

    # ---------------------------------------------------------------- one span per exception passenger
    by_pid: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for a in attempts:
        by_pid[a["passenger_id"]].append(a)
    decisions = {d["passenger_id"]: d for d in (approval or {}).get("exception_decisions") or []}
    exceptions = {}
    for pid in sorted(set(exc_children) | set(by_pid)):
        children = exc_children.get(pid, [])
        tries = sorted(by_pid.get(pid, []), key=lambda a: a["attempt"])
        final = next((a for a in tries if a["final"]), None)
        s_start = min((_ts(c["start"]) for c in children), default=decided_at)
        s_end = max((_ts(c["end"]) for c in children), default=decided_at)
        exc_id = f"exception-{pid}"
        summary = {
            "attempts": len(tries),
            "first_attempt_verdict": tries[0]["verdict"] if tries else None,
            "final_action": final["action"] if final else None,
            "final_verdict": final["verdict"] if final else None,
            "violations": [v["code"] for a in tries for v in a["violations"]],
            "operator_decision": decisions.get(pid, {}).get("decision"),
            "planner": final["planner"] if final else None,
            "prompt_version": final["prompt_version"] if final else None,
        }
        exceptions[pid] = summary
        spans.append(
            _span(
                exc_id,
                root_id,
                f"exception {pid}",
                "exception",
                s_start,
                s_end,
                status="ERROR" if tries and not final else "OK",
                **{f"reroute.exception.{k}": v for k, v in summary.items() if v not in (None, [])},
                **{"reroute.exception.passenger_id": pid},
            )
        )
        for c in children:
            c["parent_id"] = exc_id

    llm_attrs = [s["attributes"] for s in llm_spans]
    summary = {
        "duration_ms": round((end - start).total_seconds() * 1000, 1),
        "llm_calls": len(llm_spans),
        "input_tokens": sum(a.get("gen_ai.usage.input_tokens") or 0 for a in llm_attrs),
        "output_tokens": sum(a.get("gen_ai.usage.output_tokens") or 0 for a in llm_attrs),
        "llm_latency_ms": round(sum(s["duration_ms"] for s in llm_spans), 1),
        "tool_calls": len(tool_spans),
        "tool_errors": sum(s["status"] == "ERROR" for s in tool_spans),
        "guardrails": sum(e["type"] == "GUARDRAIL" for e in events),
        "exceptions": exceptions,
    }
    root = _span(
        root_id,
        None,
        "recovery_task",
        "task",
        start,
        end,
        status="ERROR" if task["state"] == "FAILED" else "OK",
        **{
            "reroute.task.id": task["id"],
            "reroute.task.command": task["command"],
            "reroute.task.state": task["state"],
            "reroute.task.flight_no": task.get("flight_no"),
            "reroute.plan.id": task.get("plan_id"),
            "gen_ai.usage.input_tokens": summary["input_tokens"],
            "gen_ai.usage.output_tokens": summary["output_tokens"],
        },
    )
    # decisions hang under their exception span; any without one (no recommendation) under the root
    for s in spans:
        if s["parent_id"] is None:
            s["parent_id"] = root_id
    ordered = sorted(spans, key=lambda s: (s["start"], s["kind"] != "exception"))  # a parent before its children
    return {"trace_id": task["id"], "summary": summary, "spans": [root, *ordered]}
