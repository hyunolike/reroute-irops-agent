"""Deterministic scripted planner used in DEMO/offline mode and tests.

It speaks the same message/tool-call protocol as Nemotron so the orchestrator, guardrails and tools are
exercised identically. It is clearly labelled `mock-llm` everywhere in the UI and audit log.
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.providers.llm.base import LLMProvider, LLMResponse, ToolCall

# Hangul counts as a word character, so "KE123편" has no \b - use explicit look-arounds.
_FLIGHT_NO = re.compile(r"(?<![A-Z0-9])((?=[A-Z0-9]{2})(?:[A-Z]{2}|[A-Z]\d|\d[A-Z]))\s?(\d{2,4})(?!\d)")

POLICY_QUERIES = [
    "airline fault cancellation re-protection own carrier same destination",
    "interline partner carrier agreement",
    "maximum re-accommodation window hours",
    "minimum connection time NRT transfer",
    "at-risk connection margin review",
    "special assistance wheelchair unaccompanied minor",
    "business class cabin downgrade",
    "VIP priority re-accommodation",
    "co-terminal airport HND NRT",
    "duty of care meal voucher refund",
    "approval before tickets are re-issued",
]


class MockLLMProvider(LLMProvider):
    name = "mock-llm"
    nvidia = False
    model = "scripted-planner-v1"

    def _called(self, messages: list[dict[str, Any]]) -> dict[str, list[dict]]:
        called: dict[str, list[dict]] = {}
        results: dict[str, str] = {}
        for m in messages:
            if m["role"] == "tool":
                results[m["tool_call_id"]] = m["content"]
        for m in messages:
            for tc in m.get("tool_calls") or []:
                called.setdefault(tc["function"]["name"], []).append(
                    {"args": json.loads(tc["function"]["arguments"]), "result": results.get(tc["id"], "")}
                )
        return called

    @staticmethod
    def _last_json(entries: list[dict]) -> dict:
        try:
            return json.loads(entries[-1]["result"])
        except (ValueError, KeyError, IndexError):
            return {}

    async def chat(self, messages, tools=None, *, task_id=None, max_tokens=2048) -> LLMResponse:
        if not tools:
            return LLMResponse(content=None, model=self.model)  # briefing is templated by the orchestrator
        called = self._called(messages)
        user = next((m["content"] for m in messages if m["role"] == "user"), "")
        m = _FLIGHT_NO.search(user.upper())
        flight_no = f"{m.group(1)}{m.group(2)}" if m else None

        def call(name: str, **args: Any) -> ToolCall:
            n = sum(len(v) for v in called.values())
            return ToolCall(id=f"call_{n}_{name}", name=name, arguments=args, raw_arguments=json.dumps(args))

        if "get_disrupted_flight" not in called:
            if not flight_no:
                return LLMResponse(content="Please provide a flight number (e.g. KE123).", model=self.model)
            return LLMResponse(
                content=f"Goal: recover passengers of {flight_no}. First I verify the flight's operational status.",
                tool_calls=[call("get_disrupted_flight", flight_no=flight_no)],
                model=self.model,
            )
        flight = self._last_json(called["get_disrupted_flight"])
        if "error" in flight:
            return LLMResponse(content=f"Could not load the flight: {flight['error']}", model=self.model)
        if flight.get("requires_recovery") is None:  # DELAYED - consult the threshold policy first
            if "search_rebooking_policy" not in called:
                return LLMResponse(
                    content=f"{flight['flight_no']} is delayed {flight.get('delay_minutes')} min. "
                    "Checking the rebooking threshold policy before deciding.",
                    tool_calls=[call("search_rebooking_policy", query="rebooking threshold for delayed flights")],
                    model=self.model,
                )
            hits = self._last_json(called["search_rebooking_policy"]).get("hits", [])
            threshold = next(
                (
                    h["params"]["rebooking_threshold_delay_minutes"]
                    for h in hits
                    if "rebooking_threshold_delay_minutes" in h.get("params", {})
                ),
                None,
            )
            pid = next((h["policy_id"] for h in hits if "rebooking_threshold_delay_minutes" in h.get("params", {})), "?")
            if threshold is None or flight.get("delay_minutes", 0) < threshold:
                return LLMResponse(
                    content=f"Delay of {flight.get('delay_minutes')} min is below the {threshold}-min rebooking "
                    f"threshold [{pid}]. Passengers stay on {flight['flight_no']}; no re-accommodation required.",
                    model=self.model,
                )
        elif not flight.get("requires_recovery"):
            return LLMResponse(
                content=f"{flight.get('flight_no')} is {flight.get('status')} - {flight.get('assessment')}. "
                "No re-accommodation is required.",
                model=self.model,
            )
        if "get_affected_passengers" not in called:
            return LLMResponse(
                content=f"{flight['flight_no']} is {flight['status']} ({flight.get('reason')}). Loading the manifest.",
                tool_calls=[call("get_affected_passengers", flight_no=flight["flight_no"])],
                model=self.model,
            )
        if "search_alternative_flights" not in called:
            return LLMResponse(
                content="Searching same-day alternatives on the route, including co-terminals for completeness.",
                tool_calls=[
                    call(
                        "search_alternative_flights",
                        origin=flight["origin"],
                        destination=flight["destination"],
                        departure_date=flight["departure_date"],
                    )
                ],
                model=self.model,
            )
        if "search_rebooking_policy" not in called:
            return LLMResponse(
                content="Retrieving the governing airline policies before any allocation decision.",
                tool_calls=[call("search_rebooking_policy", query=q) for q in POLICY_QUERIES],
                model=self.model,
            )
        if "optimize_rebooking" not in called:
            return LLMResponse(
                content="Constraints are grounded in retrieved policies. Delegating the allocation to the solver.",
                tool_calls=[call("optimize_rebooking", flight_no=flight["flight_no"])],
                model=self.model,
            )
        if "propose_rebooking" not in called:
            return LLMResponse(
                content="Solver returned an allocation. Submitting it to the operator for approval.",
                tool_calls=[call("propose_rebooking", flight_no=flight["flight_no"])],
                model=self.model,
            )
        return LLMResponse(content="Plan submitted - waiting for operator approval.", model=self.model)
