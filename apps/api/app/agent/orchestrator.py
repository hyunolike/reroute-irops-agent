"""ReRoute agent orchestrator.

Nemotron (via NIM) plans and selects tools; the orchestrator enforces the workflow contract:
schema-validated arguments, tool preconditions, a step budget, the agent state machine, and the rule
that state-changing tools only run after human approval. Every transition is written to the event log
that the dashboard streams over SSE.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any

from pydantic import ValidationError

from app.agent.prompts import BRIEFING_PROMPT, SYSTEM_PROMPT
from app.approval.gateway import ApprovalError, ApprovalGateway
from app.domain.enums import AgentState, AssignmentStatus, Component, EventType
from app.providers.llm.base import LLMError, LLMProvider, LLMResponse, ToolCall
from app.providers.llm.mock import MockLLMProvider
from app.repositories.agent import AgentRepository
from app.security.governed_http import GovernedHttpClient, PolicyViolation
from app.tools.base import AgentMemory, ToolContext, ToolError, ToolRegistry

log = logging.getLogger("reroute.agent")
MAX_STEPS = 14
_POLICY_ID = re.compile(r"\[([A-Z]{2,5}-\d{3})\]")


class AgentOrchestrator:
    def __init__(
        self,
        *,
        repo: AgentRepository,
        llm: LLMProvider,
        tools: ToolRegistry,
        gateway: ApprovalGateway,
        http: GovernedHttpClient,
        agent_name: str,
        security_component: Component,
        component_overrides: dict[str, Component] | None = None,
        step_delay_ms: int = 0,
    ) -> None:
        self.repo = repo
        self.llm = llm
        self.tools = tools
        self.gateway = gateway
        self.http = http
        self.agent = agent_name
        self.security_component = security_component
        # Badge each tool with the provider that is ACTUALLY configured (never claim NVIDIA when on fallback)
        self.component_overrides = component_overrides or {}
        self.step_delay_ms = step_delay_ms
        self._state: dict[str, str] = {}

    # ------------------------------------------------------------------ helpers
    def _llm_component(self, llm: LLMProvider) -> Component:
        return Component.NEMOTRON if llm.nvidia else Component.MOCK_LLM

    def _emit(self, task_id: str, type: EventType, component: Component, title: str, detail=None, duration_ms=None):
        state = self._state.get(task_id, AgentState.RECEIVED.value)
        self.repo.add_event(
            task_id, type=type.value, state=state, component=component.value, title=title, detail=detail, duration_ms=duration_ms
        )

    def _set_state(self, task_id: str, state: AgentState, note: str | None = None) -> None:
        if self._state.get(task_id) == state.value:
            return
        self._state[task_id] = state.value
        self.repo.update_task(task_id, state=state.value)
        self._emit(task_id, EventType.STATE_CHANGED, Component.ORCHESTRATOR, note or state.value, {"state": state.value})

    # ------------------------------------------------------------------ main loop
    async def run(self, task_id: str) -> None:
        task = self.repo.get_task(task_id)
        assert task is not None
        memory = AgentMemory()
        llm_ref: dict[str, LLMProvider] = {"llm": self.llm}
        ctx = ToolContext(task_id=task_id, agent=self.agent, memory=memory, http=self.http, gateway=self.gateway)
        ctx.write_briefing = lambda: self._write_briefing(task_id, memory, llm_ref)
        self._state[task_id] = ""
        self._set_state(task_id, AgentState.RECEIVED, f"Goal received: “{task.command}”")
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": task.command},
        ]
        schemas = self.tools.schemas()
        nudged = False
        try:
            for _ in range(MAX_STEPS):
                resp = await self._plan(task_id, llm_ref, messages, schemas)
                if resp.content:
                    self._emit(
                        task_id,
                        EventType.PLANNER,
                        self._llm_component(llm_ref["llm"]),
                        _one_line(resp.content),
                        {
                            "model": resp.model,
                            "latency_ms": round(resp.latency_ms, 1),
                            "reasoning": (resp.reasoning or "")[:2000],
                        },
                        resp.latency_ms,
                    )
                if not resp.tool_calls:
                    if memory.plan_id:
                        break
                    if memory.flight is not None and memory.flight_assessment in {"no_recovery", "check_policy"}:
                        return self._complete_without_action(task_id, memory, resp.content or "")
                    if memory.flight is None:
                        raise RuntimeError(resp.content or "could not identify or verify the disrupted flight")
                    if nudged:
                        self._emit(
                            task_id,
                            EventType.GUARDRAIL,
                            Component.ORCHESTRATOR,
                            "Planner stopped early twice → switching to deterministic planner",
                        )
                        llm_ref["llm"] = MockLLMProvider()
                    nudged = True
                    messages.append({"role": "user", "content": "Continue the workflow by calling the next required tool."})
                    continue
                messages.append(_assistant_message(resp))
                for tc in resp.tool_calls:
                    content = await self._execute(task_id, tc, ctx)
                    messages.append({"role": "tool", "tool_call_id": tc.id, "content": content})
                if memory.plan_id:
                    break
            else:
                raise RuntimeError(f"step budget of {MAX_STEPS} exhausted before a plan was proposed")
            self._set_state(task_id, AgentState.WAITING_APPROVAL, "Waiting for operator approval")
            self.repo.update_task(task_id, plan_id=memory.plan_id, flight_no=memory.flight.flight_no if memory.flight else None)
            self._emit(
                task_id,
                EventType.APPROVAL,
                Component.APPROVAL_GATEWAY,
                "Approval requested - booking changes are blocked until an operator approves",
                {"plan_id": memory.plan_id, "approval_id": memory.approval_id},
            )
        except Exception as e:  # noqa: BLE001 - any failure must be visible, never silent
            log.exception("agent task %s failed", task_id)
            self._set_state(task_id, AgentState.FAILED, f"Failed: {e}")
            self.repo.update_task(task_id, error=str(e))

    async def _plan(self, task_id, llm_ref, messages, schemas) -> LLMResponse:
        llm = llm_ref["llm"]
        try:
            return await llm.chat(messages, schemas, task_id=task_id)
        except (LLMError, PolicyViolation, Exception) as e:  # noqa: BLE001
            if isinstance(llm, MockLLMProvider):
                raise
            self._emit(
                task_id,
                EventType.GUARDRAIL,
                Component.ORCHESTRATOR,
                f"Nemotron unavailable ({type(e).__name__}) → deterministic planner fallback for this task",
                {"error": str(e)[:500]},
            )
            self.repo.update_task(
                task_id, runtime={**(self.repo.get_task(task_id).runtime or {}), "planner_fallback": str(e)[:200]}
            )
            llm_ref["llm"] = MockLLMProvider()
            return await llm_ref["llm"].chat(messages, schemas, task_id=task_id)

    async def _execute(self, task_id: str, tc: ToolCall, ctx: ToolContext) -> str:
        tool = self.tools.get(tc.name)
        if tool is None:
            self._emit(task_id, EventType.GUARDRAIL, Component.ORCHESTRATOR, f"Rejected unknown tool '{tc.name}'")
            return json.dumps({"error": f"unknown tool {tc.name}", "available": self.tools.names()})
        try:
            args = tool.Args.model_validate(tc.arguments)
        except ValidationError as e:
            self._emit(
                task_id,
                EventType.GUARDRAIL,
                Component.ORCHESTRATOR,
                f"Rejected invalid arguments for {tc.name}",
                {"errors": json.loads(e.json())},
            )
            return json.dumps({"error": "invalid arguments", "details": json.loads(e.json())})
        pre = tool.precondition(ctx)
        if pre:
            self._emit(task_id, EventType.GUARDRAIL, Component.ORCHESTRATOR, f"{tc.name} blocked: {pre}")
            return json.dumps({"error": f"precondition failed: {pre}"})
        if not tool.requires_approval:
            self._set_state(task_id, tool.state)
        args_view = args.model_dump(mode="json")
        component = self.component_overrides.get(tc.name, tool.component)
        self._emit(
            task_id,
            EventType.TOOL_CALL,
            component,
            f"{tc.name}({_fmt_args(args_view)})",
            {"tool": tc.name, "args": args_view},
        )
        start = time.perf_counter()
        try:
            result = await tool.run(args, ctx)
        except PolicyViolation as e:
            self._emit(
                task_id,
                EventType.TOOL_ERROR,
                self.security_component,
                f"Blocked by sandbox policy: {e}",
                {"tool": tc.name, "policy": e.decision.policy},
            )
            return json.dumps({"error": str(e)})
        except ApprovalError as e:
            self._emit(
                task_id,
                EventType.TOOL_ERROR,
                Component.APPROVAL_GATEWAY,
                f"{tc.name} denied: {e}",
                {"tool": tc.name, "code": e.code},
            )
            return json.dumps({"error": str(e), "code": e.code})
        except (ToolError, Exception) as e:  # noqa: BLE001
            self._emit(task_id, EventType.TOOL_ERROR, component, f"{tc.name} failed: {e}", {"tool": tc.name})
            return json.dumps({"error": str(e)})
        ms = (time.perf_counter() - start) * 1000
        if self.step_delay_ms:
            await asyncio.sleep(self.step_delay_ms / 1000)  # demo pacing only - keeps the live timeline readable
        self._emit(
            task_id,
            EventType.TOOL_RESULT,
            result.component or component,
            result.title,
            {"tool": tc.name, **result.detail},
            ms,
        )
        return json.dumps(result.llm_view, default=str)

    # ------------------------------------------------------------------ briefing (LLM explains, never decides)
    async def _write_briefing(self, task_id: str, memory: AgentMemory, llm_ref: dict) -> str:
        res = memory.optimization
        assert res is not None and memory.flight is not None
        facts = {
            "flight": {
                "flight_no": memory.flight.flight_no,
                "status": memory.flight.status,
                "reason": memory.flight.disruption.reason if memory.flight.disruption else None,
            },
            "summary": res.summary.model_dump(),
            "baseline": res.baseline.model_dump(),
            "solver": res.solver,
            "excluded_flights": [e.model_dump() for e in res.excluded_flights],
            "exceptions": [
                {"passenger": a.name, "status": a.status.value, "reason": a.reason, "policies": a.policy_ids}
                for a in res.assignments
                if a.status != AssignmentStatus.AUTO_ASSIGNED
            ],
            "policies": {pid: h.title for pid, h in sorted(memory.policy_hits.items())},
        }
        llm = llm_ref["llm"]
        text = ""
        if llm.nvidia:
            try:
                resp = await llm.chat(
                    [
                        {"role": "system", "content": BRIEFING_PROMPT},
                        {"role": "user", "content": json.dumps(facts, ensure_ascii=False, default=str)},
                    ],
                    None,
                    task_id=task_id,
                    max_tokens=900,
                )
                text = (resp.content or "").strip()
                cited = set(_POLICY_ID.findall(text))
                unknown = cited - set(memory.policy_hits)
                if unknown:
                    self._emit(
                        task_id,
                        EventType.GUARDRAIL,
                        Component.ORCHESTRATOR,
                        f"Briefing cited unretrieved policies {sorted(unknown)} → replaced with grounded template",
                    )
                    text = ""
                else:
                    self._emit(
                        task_id,
                        EventType.PLANNER,
                        Component.NEMOTRON,
                        f"Operator briefing written by {resp.model} (citations verified: {', '.join(sorted(cited)) or 'none'})",
                        {"latency_ms": round(resp.latency_ms, 1)},
                        resp.latency_ms,
                    )
            except Exception as e:  # noqa: BLE001
                self._emit(task_id, EventType.GUARDRAIL, Component.ORCHESTRATOR, f"Briefing generation failed ({e}) → template")
        return text or template_briefing(facts)

    # ------------------------------------------------------------------ outcomes
    def _complete_without_action(self, task_id: str, memory: AgentMemory, reasoning: str) -> None:
        f = memory.flight
        report = {
            "outcome": "NO_ACTION_REQUIRED",
            "flight_no": f.flight_no if f else None,
            "status": f.status if f else None,
            "reasoning": reasoning,
            "policies": sorted(memory.policy_hits),
        }
        self.repo.update_task(task_id, report=report, flight_no=f.flight_no if f else None)
        self._emit(
            task_id,
            EventType.REPORT,
            Component.ORCHESTRATOR,
            f"No re-accommodation required for {f.flight_no if f else '?'}",
            report,
        )
        self._set_state(task_id, AgentState.COMPLETED, "Completed - no action required")

    async def resume_after_approval(self, task_id: str, plan_id: str) -> None:
        self._state[task_id] = self.repo.get_task(task_id).state
        approval = self.gateway.get_approval(plan_id)
        self._emit(
            task_id,
            EventType.APPROVAL,
            Component.APPROVAL_GATEWAY,
            f"Plan approved by {approval.approved_by}" + (f" - “{approval.comment}”" if approval.comment else ""),
            {"approval_id": approval.id, "approved_manual_items": approval.approved_manual_item_ids},
        )
        self._set_state(task_id, AgentState.EXECUTING, "Executing approved plan")
        ctx = ToolContext(
            task_id=task_id, agent=self.agent, memory=AgentMemory(plan_id=plan_id), http=self.http, gateway=self.gateway
        )
        content = json.loads(
            await self._execute(task_id, ToolCall(id="exec", name="execute_rebooking", arguments={"plan_id": plan_id}), ctx)
        )
        if "error" in content:
            self._set_state(task_id, AgentState.FAILED, f"Execution failed: {content['error']}")
            self.repo.update_task(task_id, error=content["error"])
            return
        report = self.final_report(plan_id)
        self.repo.update_task(task_id, report=report)
        self._emit(
            task_id,
            EventType.REPORT,
            Component.ORCHESTRATOR,
            f"Recovery complete: {report['rebooked']} rebooked · {report['held_for_operator']} held for operator · "
            f"{report['no_feasible']} need alternative handling",
            report,
        )
        self._set_state(task_id, AgentState.COMPLETED, "Completed")

    def on_rejected(self, task_id: str, plan_id: str) -> None:
        self._state[task_id] = self.repo.get_task(task_id).state
        approval = self.gateway.get_approval(plan_id)
        self._emit(
            task_id,
            EventType.APPROVAL,
            Component.APPROVAL_GATEWAY,
            f"Plan rejected by {approval.approved_by}" + (f" - “{approval.comment}”" if approval.comment else ""),
            {"approval_id": approval.id},
        )
        self.repo.update_task(task_id, report={"outcome": "REJECTED", "plan_id": plan_id, "comment": approval.comment})
        self._set_state(task_id, AgentState.REJECTED, "Plan rejected - no booking changed")

    def final_report(self, plan_id: str) -> dict[str, Any]:
        plan = self.gateway.get_plan(plan_id)
        items = plan.items
        by = lambda st: [i for i in items if i.status == st]  # noqa: E731
        rebooked = by(AssignmentStatus.EXECUTED.value)
        return {
            "outcome": plan.status,
            "plan_id": plan.id,
            "flight_no": plan.flight_no,
            "affected": len(items),
            "rebooked": len(rebooked),
            "failed": len(by(AssignmentStatus.EXECUTION_FAILED.value)),
            "held_for_operator": len(by(AssignmentStatus.HELD_FOR_OPERATOR.value)),
            "no_feasible": len(by(AssignmentStatus.NO_FEASIBLE.value)),
            "by_flight": {
                f: sum(1 for i in rebooked if i.alternative_flight == f)
                for f in sorted({i.alternative_flight for i in rebooked if i.alternative_flight})
            },
            "follow_ups": [
                *[f"VIP contact: {i.passenger_name} → {i.alternative_flight} [VIP-002]" for i in rebooked if i.vip],
                *[
                    f"Operator handling: {i.passenger_name} ({i.reason})"
                    for i in items
                    if i.status in (AssignmentStatus.HELD_FOR_OPERATOR.value, AssignmentStatus.NO_FEASIBLE.value)
                ],
                f"Meal vouchers for {sum(1 for i in rebooked if (i.delay_minutes or 0) >= 180)} passengers delayed ≥ 3h [IROP-004]",
            ],
        }


# ---------------------------------------------------------------------- utilities
def _assistant_message(resp: LLMResponse) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": resp.content or "",
        "tool_calls": [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.name, "arguments": tc.raw_arguments or json.dumps(tc.arguments)},
            }
            for tc in resp.tool_calls
        ],
    }


def _one_line(text: str, n: int = 240) -> str:
    t = " ".join(text.split())
    return t if len(t) <= n else t[: n - 1] + "…"


def _fmt_args(args: dict[str, Any]) -> str:
    return ", ".join(f"{k}={v!r}" if isinstance(v, str) else f"{k}={v}" for k, v in args.items())


def _hm(minutes: float) -> str:
    m = int(round(minutes))
    return f"{m // 60}h {m % 60:02d}m"


def template_briefing(facts: dict[str, Any]) -> str:
    s, b, f = facts["summary"], facts["baseline"], facts["flight"]
    pol = facts["policies"]
    cite = lambda *ids: " ".join(f"[{i}]" for i in ids if i in pol)  # noqa: E731
    lines = [
        "**상황 요약**",
        f"- {f['flight_no']} {f['status']} ({f['reason']}). 영향 승객 {s['affected']}명. 항공사 귀책으로 자사편 우선 재보호 {cite('IROP-001')}.",
        "- 정책상 제외된 항공편: "
        + ", ".join(f"{e['flight_no']}({e['policy_id'] or e['constraint']})" for e in facts["excluded_flights"]),
        "",
        "**최적화 결과**",
        f"- {facts['solver']}: 자동 배정 {s['auto_assigned']}명 · 운영자 검토 {s['manual_review']}명 · 대안 없음 {s['no_feasible']}명.",
        f"- 연결 승객 {s['connections_protected']}명 MCT 충족 {cite('MCT-002')} · VIP 평균 지연 {_hm(s['vip_avg_delay_minutes'])} {cite('VIP-001')}.",
        f"- 수작업 선착순 방식 대비: 연결 실패 {b['missed_connections']}→0, SSR 위반 {b['ssr_violations']}→0, 비즈니스 다운그레이드 {b['business_downgrades']}→{s['business_downgrades']} {cite('FARE-002')}.",
        "",
        "**운영자 확인 필요 사항**",
    ]
    for e in facts["exceptions"]:
        ids = " ".join(f"[{p}]" for p in e["policies"] if p in pol)
        lines.append(f"- {e['passenger']} ({e['status']}): {e['reason']} {ids}")
    return "\n".join(lines)
