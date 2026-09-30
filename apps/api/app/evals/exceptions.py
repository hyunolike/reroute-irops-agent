"""Exception-resolution evaluation: run the configured planner over the golden set, score it, apply release gates.

    python -m app.evals.exceptions                         # scripted planner unless NVIDIA_API_KEY is set
    NVIDIA_API_KEY=nvapi-... python -m app.evals.exceptions --repeats 3 --json report.json

Each case is the seeded demo day plus the edits in data/evals/exception_golden.yaml. The agent runs end to end in
process (assist mode, so the control plane also re-verifies what the operator would see). Before anything is scored,
the labels are checked against the solver and the verifier (golden-set drift); a stale label fails the run instead of
silently rewarding or punishing the model. Exit code 1 when a gate in config/eval_gates.yaml fails.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import tempfile
import time
from collections import Counter, defaultdict
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import yaml

from app.agent.evaluate import load_llm_env
from app.config import _DEFAULT_ROOT, Settings
from app.evals.mutations import apply_mutations
from app.main import create_app
from app.resolution.baseline import baseline_proposal
from app.resolution.models import FLIGHT_ACTIONS, ResolutionAction
from app.seed.loader import seed_database

GOLDEN = _DEFAULT_ROOT / "data" / "evals" / "exception_golden.yaml"
GATES = _DEFAULT_ROOT / "config" / "eval_gates.yaml"
SERVICE_DATE = date(2026, 10, 15)
EXCEPTION_STATUSES = ("MANUAL_REVIEW", "NO_FEASIBLE")
_POLICY_ID = re.compile(r"\b[A-Z]{2,5}-\d{3}\b")


# ---------------------------------------------------------------------------------------------- running
async def run_case(
    case: dict[str, Any], workdir: Path, repeat: int, base: dict[str, Any], planner: Callable[[], Any] | None = None
) -> dict[str, Any]:
    settings = Settings(
        database_url=f"sqlite:///{workdir / f'{case["id"]}-{repeat}.db'}",
        demo_service_date=SERVICE_DATE,
        seed_on_startup=False,
        airline_api_base_url="http://airline-service:8000",
        reroute_api_base_url="http://reroute-api:8000",
        agent_step_delay_ms=0,
        exception_resolution_mode="assist",
        **base,
    )
    app = create_app(settings)
    c = app.state.container
    if planner:  # tests score deliberately bad planners
        c.llm = c.orchestrator.llm = planner()
    c.http.transport = httpx.ASGITransport(app=app)
    c.db.create_all()
    with c.db.session() as s:
        seed_database(s, settings.seed_path, settings.demo_service_date)
        apply_mutations(s, case.get("mutations") or [])
    started = time.perf_counter()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://eval") as client:
        task = (await client.post("/api/agent/tasks", json={"command": case["command"]})).json()
        await c.runner.drain()
        task = (await client.get(f"/api/agent/tasks/{task['id']}")).json()
        events = (await client.get(f"/api/agent/tasks/{task['id']}/events", params={"stream": False})).json()["events"]
        trace = (await client.get(f"/api/agent/tasks/{task['id']}/trace")).json()
        plan = res = None
        if task.get("plan_id"):
            plan = (await client.get(f"/api/rebooking/plans/{task['plan_id']}")).json()
            res = (await client.get(f"/api/rebooking/plans/{task['plan_id']}/exception-resolutions")).json()
    return {
        "case": case["id"],
        "repeat": repeat,
        "state": task["state"],
        "error": task.get("error"),
        "planner": f"{c.llm.name}/{c.llm.model}",
        "seconds": round(time.perf_counter() - started, 1),
        "exceptions": sorted(i["passenger_id"] for i in (plan or {}).get("items", []) if i["status"] in EXCEPTION_STATUSES),
        "views": {
            e["detail"]["passenger"]["passenger_id"]: e["detail"]
            for e in events
            if e["type"] == "TOOL_RESULT" and e["detail"].get("tool") == "explore_exception_options"
        },
        "attempts": (res or {}).get("attempts", []),
        "recommendations": (res or {}).get("recommendations", []),
        "cost": {k: trace["summary"][k] for k in ("llm_calls", "input_tokens", "output_tokens", "llm_latency_ms")},
    }


# ---------------------------------------------------------------------------------------------- scoring
def _correct(label: dict[str, Any], action: str | None, flight: str | None) -> bool:
    if action not in label["acceptable"]:
        return False
    return action not in FLIGHT_ACTIONS or not label.get("flights") or flight in label["flights"]


def label_drift(case: dict[str, Any], run: dict[str, Any]) -> list[str]:
    """Why the labels no longer describe what the solver / verifier produce for this case (empty = fine)."""
    labels = case["exceptions"]
    problems = []
    if run["state"] != "WAITING_APPROVAL":
        return [f"agent ended in {run['state']}: {run['error']}"]
    if set(run["exceptions"]) != set(labels):
        problems.append(f"solver exceptions {run['exceptions']} != labelled {sorted(labels)}")
    for pid, label in labels.items():
        view = run["views"].get(pid)
        if view is None:
            continue
        options = {o["flight_no"]: o for o in view["options"]}
        for flight in label.get("flights", []):
            o = options.get(flight)
            ok = o is not None and (
                (ResolutionAction.REQUEST_POLICY_WAIVER in label["acceptable"] and o["waivable_by_duty_manager"])
                or (ResolutionAction.REASSIGN_TO_OPTION in label["acceptable"] and o["feasible_under_policy"])
            )
            if not ok:
                problems.append(f"{pid}: labelled flight {flight} is not a feasible or waivable option")
    return problems


def score_run(case: dict[str, Any], run: dict[str, Any], context_policies: set[str]) -> list[dict[str, Any]]:
    finals = {a["passenger_id"]: a for a in run["attempts"] if a["final"]}
    firsts = {a["passenger_id"]: a for a in run["attempts"] if a["attempt"] == 1}
    control = {r["passenger_id"]: r["verdict"] for r in run["recommendations"]}
    rows = []
    for pid, label in case["exceptions"].items():
        final = finals.get(pid)
        action = final["action"] if final else None
        flight = final["proposal"].get("flight_no") if final else None
        view = run["views"].get(pid)
        base = baseline_proposal(view) if view else None
        relevant = set(_POLICY_ID.findall(json.dumps(view))) | context_policies if view else context_policies
        cited = final["proposal"].get("policy_ids", []) if final else []
        rows.append(
            {
                "case": case["id"],
                "repeat": run["repeat"],
                "passenger_id": pid,
                "action": action,
                "flight": flight,
                "first_verdict": firsts[pid]["verdict"] if pid in firsts else None,
                "attempts": sum(a["passenger_id"] == pid for a in run["attempts"]),
                "correct": _correct(label, action, flight),
                "preferred": (action == label["preferred"]) if label.get("preferred") else None,
                "baseline_action": base.action.value if base else None,
                "baseline_correct": _correct(label, base.action.value, base.flight_no) if base else False,
                "cited": len(cited),
                "cited_relevant": sum(p in relevant for p in cited),
                # shown to an operator although rejected: by the planner's verifier or the control plane's
                "bypass": bool(final and (final["verdict"] == "REJECTED" or control.get(pid) == "REJECTED")),
            }
        )
    return rows


def _rate(num: float, den: float) -> float | None:
    return round(num / den, 3) if den else None


def aggregate(rows: list[dict[str, Any]], drift: list[str], runs: list[dict[str, Any]]) -> dict[str, Any]:
    with_attempt = [r for r in rows if r["first_verdict"]]
    by_pair: dict[tuple[str, str], set] = defaultdict(set)
    for r in rows:
        by_pair[(r["case"], r["passenger_id"])].add((r["action"], r["flight"]))
    preferred = [r for r in rows if r["preferred"] is not None]
    cost = [r["cost"] for r in runs]
    return {
        "golden_set_drift": len(drift),
        "verifier_bypass": sum(r["bypass"] for r in rows),
        "coverage": _rate(sum(r["action"] is not None for r in rows), len(rows)),
        "first_pass_accept_rate": _rate(sum(r["first_verdict"] != "REJECTED" for r in with_attempt), len(with_attempt)),
        "action_accuracy": _rate(sum(r["correct"] for r in rows), len(rows)),
        "baseline_action_accuracy": _rate(sum(r["baseline_correct"] for r in rows), len(rows)),
        "preferred_rate": _rate(sum(r["preferred"] for r in preferred), len(preferred)),
        "citation_precision": _rate(sum(r["cited_relevant"] for r in rows), sum(r["cited"] for r in rows)),
        "consistency": _rate(sum(len(v) == 1 for v in by_pair.values()), len(by_pair)),
        "actions": dict(Counter(r["action"] or "none" for r in rows)),
        "per_case_avg": {
            k: round(sum(x[k] or 0 for x in cost) / len(cost), 1) if cost else 0
            for k in ("llm_calls", "input_tokens", "output_tokens", "llm_latency_ms")
        },
    }


def check_gates(metrics: dict[str, Any], gates: dict[str, dict[str, float]]) -> list[str]:
    failures = []
    for name, bound in gates.items():
        value = metrics.get(name)
        if value is None:
            failures.append(f"{name}: no data")
        elif "min" in bound and value < bound["min"]:
            failures.append(f"{name}: {value} < {bound['min']}")
        elif "max" in bound and value > bound["max"]:
            failures.append(f"{name}: {value} > {bound['max']}")
    return failures


async def evaluate(
    golden: dict[str, Any],
    gates: dict[str, Any],
    *,
    repeats: int = 1,
    only: set[str] | None = None,
    base: dict[str, Any] | None = None,
    planner: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    cases = [c for c in golden["cases"] if not only or c["id"] in only]
    context = set(golden.get("context_policies") or [])
    runs, rows, drift = [], [], []
    with tempfile.TemporaryDirectory() as tmp:
        for case in cases:
            for i in range(repeats):
                run = await run_case(case, Path(tmp), i, dict(base or {}), planner)
                runs.append(run)
                problems = label_drift(case, run)
                drift += [f"{case['id']}#{i}: {p}" for p in problems]
                if not problems:
                    rows += score_run(case, run, context)
    metrics = aggregate(rows, drift, runs)
    failures = check_gates(metrics, gates["gates"])
    return {
        "planner": runs[0]["planner"] if runs else None,
        "cases": len(cases),
        "repeats": repeats,
        "metrics": metrics,
        "gates": gates["gates"],
        "gate_failures": failures,
        "passed": not failures,
        "drift": drift,
        "rows": rows,
    }


# ---------------------------------------------------------------------------------------------- CLI
def _print(report: dict[str, Any]) -> None:
    m = report["metrics"]
    print(f"\nException-resolution eval — planner {report['planner']} · {report['cases']} cases × {report['repeats']}")
    if not str(report["planner"]).startswith("nvidia"):
        print("  ⚠ Not running Nemotron (set NVIDIA_API_KEY). These are the scripted planner's scores.")
    print(f"\n  {'case':38} {'pax':5} {'action':27} {'flight':7} {'1st':21} ok  base")
    for r in report["rows"]:
        print(
            f"  {r['case']:38} {r['passenger_id']:5} {str(r['action']):27} {str(r['flight'] or ''):7} "
            f"{str(r['first_verdict']):21} {'✓' if r['correct'] else '✗'}   {'✓' if r['baseline_correct'] else '✗'}"
        )
    for d in report["drift"]:
        print(f"  DRIFT {d}")
    print()
    for name, bound in report["gates"].items():
        mark = "FAIL" if any(f.startswith(f"{name}:") for f in report["gate_failures"]) else "pass"
        print(f"  [{mark}] {name:24} {m.get(name)!s:8} (gate {bound})")
    print(f"         {'baseline_action_accuracy':24} {m['baseline_action_accuracy']}")
    print(f"         {'preferred_rate':24} {m['preferred_rate']}")
    print(f"         {'cost per case':24} {m['per_case_avg']}")
    print(f"\n{'PASSED' if report['passed'] else 'FAILED'}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repeats", type=int, default=1, help="runs per case (consistency needs >= 2)")
    ap.add_argument("--cases", help="comma-separated case ids (default: all)")
    ap.add_argument("--json", type=Path, help="write the full report here")
    ap.add_argument("--golden", type=Path, default=GOLDEN)
    ap.add_argument("--gates", type=Path, default=GATES)
    args = ap.parse_args()
    load_llm_env(_DEFAULT_ROOT / ".env")
    golden = yaml.safe_load(args.golden.read_text(encoding="utf-8"))
    gates = yaml.safe_load(args.gates.read_text(encoding="utf-8"))
    only = set(args.cases.split(",")) if args.cases else None
    report = asyncio.run(evaluate(golden, gates, repeats=args.repeats, only=only, base={"_env_file": None}))
    if args.json:
        args.json.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    _print(report)
    sys.exit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
