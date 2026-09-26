"""Scenario evaluation for the reasoning model (run against real Nemotron with NVIDIA_API_KEY set).

    NVIDIA_API_KEY=nvapi-... python -m app.agent.evaluate            # human-readable scorecard
    NVIDIA_API_KEY=nvapi-... python -m app.agent.evaluate --json     # machine-readable

Runs every scenario end-to-end in-process (temporary SQLite DB, real HTTP semantics via ASGI) with the LLM chosen
by the normal settings (LLM_PROVIDER=auto -> Nemotron when a key is present) and checks agentic behaviour:
final state, tools used, whether the model needed guidance or was replaced by the fallback planner, and that the
allocation still came from the solver.

The key, model and retriever settings are also read from the repo-root .env (the file docker compose uses), so
`make eval-llm` works after the one-time .env setup; variables already set in the shell take precedence.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import httpx
from dotenv import dotenv_values

from app.config import _DEFAULT_ROOT, Settings
from app.main import create_app
from app.seed.loader import seed_database


@dataclass
class Scenario:
    name: str
    command: str
    expect_state: str
    expect_tools: set[str] = field(default_factory=set)
    expect_summary: dict[str, int] = field(default_factory=dict)
    expect_policy: str | None = None


SCENARIOS = [
    Scenario(
        "KE123 cancellation (Korean)",
        "KE123편이 결항됐어. 영향 승객을 확인하고 최적 재배정안을 만들어줘.",
        "WAITING_APPROVAL",
        {
            "get_disrupted_flight",
            "get_affected_passengers",
            "search_alternative_flights",
            "search_rebooking_policy",
            "optimize_rebooking",
            "propose_rebooking",
        },
        {"affected": 35, "auto_assigned": 31, "manual_review": 3, "no_feasible": 1},
    ),
    Scenario(
        "KE123 cancellation (English)",
        "Flight KE123 got cancelled. Rebook the affected passengers optimally and prepare it for my approval.",
        "WAITING_APPROVAL",
        {"get_disrupted_flight", "optimize_rebooking", "propose_rebooking"},
        {"affected": 35, "auto_assigned": 31},
    ),
    Scenario(
        "KE125 45-min delay",
        "KE125편 지연됐는데 승객 재배정이 필요한지 확인해줘.",
        "COMPLETED",
        {"get_disrupted_flight", "search_rebooking_policy"},
        expect_policy="RBK-002",
    ),
    Scenario("Unknown flight", "ZZ999편 결항 처리해줘.", "FAILED", {"get_disrupted_flight"}),
]


async def run_scenario(sc: Scenario, workdir: Path, base: dict[str, Any]) -> dict[str, Any]:
    settings = Settings(
        database_url=f"sqlite:///{workdir / (sc.name.split()[0] + str(abs(hash(sc.name))) + '.db')}",
        demo_service_date=date(2026, 10, 15),
        seed_on_startup=False,
        airline_api_base_url="http://airline-service:8000",
        reroute_api_base_url="http://reroute-api:8000",
        agent_step_delay_ms=0,
        **base,
    )
    app = create_app(settings)
    c = app.state.container
    c.http.transport = httpx.ASGITransport(app=app)
    c.db.create_all()
    with c.db.session() as s:
        seed_database(s, settings.seed_path, settings.demo_service_date)
    started = time.perf_counter()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://eval") as client:
        task = (await client.post("/api/agent/tasks", json={"command": sc.command})).json()
        await c.runner.drain()
        task = (await client.get(f"/api/agent/tasks/{task['id']}")).json()
        events = (await client.get(f"/api/agent/tasks/{task['id']}/events", params={"stream": False})).json()["events"]
        plan = (await client.get(f"/api/rebooking/plans/{task['plan_id']}")).json() if task.get("plan_id") else None
    elapsed = time.perf_counter() - started

    tools = [e["detail"].get("tool") for e in events if e["type"] == "TOOL_CALL"]
    planner = [e for e in events if e["type"] == "PLANNER"]
    guardrails = [e["title"] for e in events if e["type"] == "GUARDRAIL"]
    fallback = bool((task.get("runtime") or {}).get("planner_fallback")) or any("takes over" in g for g in guardrails)
    checks = {
        "state": task["state"] == sc.expect_state,
        "tools": sc.expect_tools <= set(tools),
        "no_fallback": not fallback,
    }
    if sc.expect_summary and plan:
        checks["solver_result"] = all(plan["summary"].get(k) == v for k, v in sc.expect_summary.items())
    elif sc.expect_summary:
        checks["solver_result"] = False
    if sc.expect_policy:
        checks["policy_cited"] = sc.expect_policy in json.dumps(task.get("report") or {})
    return {
        "scenario": sc.name,
        "passed": all(checks.values()),
        "checks": checks,
        "state": task["state"],
        "llm": c.llm.name,
        "model": c.llm.model,
        "planner_turns": len(planner),
        "tool_calls": len(tools),
        "tool_sequence": tools,
        "guardrails": guardrails,
        "fallback": fallback,
        "seconds": round(elapsed, 1),
        "error": task.get("error"),
    }


# Only the NVIDIA model settings (reasoning + retrieval): the rest of .env (AGENT_EXECUTION, SECURITY_RUNTIME, ...)
# describes the deployed stack, not this in-process run.
_LLM_ENV = ("NVIDIA_API_KEY", "LLM_PROVIDER", "RETRIEVER_PROVIDER", "NIM_")


def load_llm_env(path: Path) -> None:
    if not path.is_file():
        return
    for k, v in dotenv_values(path).items():
        if v and k.startswith(_LLM_ENV):
            os.environ.setdefault(k, v)


async def main_async(as_json: bool) -> int:
    base = {}  # everything else (NVIDIA_API_KEY, LLM_PROVIDER, NIM_MODEL, ...) comes from the environment
    with tempfile.TemporaryDirectory() as tmp:
        results = [await run_scenario(sc, Path(tmp), base) for sc in SCENARIOS]
    if as_json:
        print(json.dumps(results, ensure_ascii=False, indent=1))
    else:
        r0 = results[0]
        print(f"\nReRoute agent evaluation — reasoning model: {r0['llm']} / {r0['model']}")
        if r0["llm"] != "nvidia-nim":
            print("  ⚠ Not running Nemotron (set NVIDIA_API_KEY). Results below are for the scripted planner.")
        for r in results:
            mark = "PASS" if r["passed"] else "FAIL"
            failed = [k for k, v in r["checks"].items() if not v]
            print(
                f"\n[{mark}] {r['scenario']}  state={r['state']}  turns={r['planner_turns']}  tools={r['tool_calls']}  {r['seconds']}s"
            )
            print(f"       tools: {' → '.join(r['tool_sequence'])}")
            if r["guardrails"]:
                print(f"       guidance/guardrails: {len(r['guardrails'])}  e.g. {r['guardrails'][0][:110]}")
            if failed:
                print(f"       failed checks: {failed}  error={r['error']}")
        print(f"\n{sum(r['passed'] for r in results)}/{len(results)} scenarios passed")
    return 0 if all(r["passed"] for r in results) else 1


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    load_llm_env(_DEFAULT_ROOT / ".env")
    sys.exit(asyncio.run(main_async(ap.parse_args().json)))


if __name__ == "__main__":
    main()
